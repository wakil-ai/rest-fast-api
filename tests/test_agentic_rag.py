from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.orchestration.flow import AgenticRAGFlow


@pytest.fixture
def mock_dependencies():
    with patch("app.orchestration.flow.ChatChain") as mock_chain, patch(
        "app.orchestration.flow.ChatMemoryService"
    ) as mock_memory, patch("app.orchestration.flow.Crews") as mock_crews:

        # Setup common mock returns
        chain_instance = mock_chain.return_value
        chain_instance.make_system_prompt = AsyncMock(
            return_value="system prompt"
        )  # Must be async

        memory_instance = mock_memory.return_value
        crews_instance = mock_crews.return_value

        # Mock crews
        crews_instance.memory_crew.return_value = MagicMock()
        crews_instance.retrieval_strategy_crew.return_value = MagicMock()
        crews_instance.evaluation_crew.return_value = MagicMock()
        crews_instance.web_search_crew.return_value = MagicMock()

        yield {
            "chain": chain_instance,
            "memory": memory_instance,
            "crews": crews_instance,
        }


@pytest.mark.asyncio
async def test_streaming_enabled_flow(mock_dependencies):
    """
    Test that the flow correctly handles streaming when enable_progress_stream=True.
    Verify that chunks are emitted via progress_callback.
    """
    # Setup
    progress_callback = AsyncMock()
    flow = AgenticRAGFlow(
        enable_progress_stream=True, progress_callback=progress_callback
    )

    # Initialize state manually as we are unit testing specific method
    flow.state.query = "test query"
    flow.state.resolved_query = "resolved query"
    flow.state.retrieval_docs = "some context"
    flow.state.memory_docs = "some memory"
    flow.state.selected_assistant = "umumiy"
    flow.state.llm_model = "gpt-4"

    # Mock ChatChain response as an async generator
    async def response_stream():
        yield "Part 1"
        yield "Part 2"

    # Configure mock chain run
    mock_dependencies["chain"].run = AsyncMock(return_value=response_stream())

    # Act: Call generate_final_answer directly
    answer = await flow.generate_final_answer()

    # Assert
    assert answer == "Part 1Part 2"
    assert flow.state.answer == "Part 1Part 2"

    # Verify chain.run was called with stream=True
    call_kwargs = mock_dependencies["chain"].run.call_args[1]
    assert call_kwargs["stream"] is True

    # Verify chunks were emitted
    # We expect progress events:
    # 1. ANSWER_GENERATION in_progress (start)
    # 2. chunk 'Part 1'
    # 3. chunk 'Part 2'

    # Getting arguments of all calls to progress_callback
    # Each call receives a formatted event (likely a dict or model dump, but checking logic)
    # The flow calls format_progress_event (imported). We should mock that too or check args passed to _emit_progress

    # Since _emit_progress calls format_progress_event, let's verify _emit_progress calls internally?
    # Or just check that progress_callback was called at least 3 times.
    assert progress_callback.call_count >= 3


@pytest.mark.asyncio
async def test_emit_progress_events(mock_dependencies):
    """Test that progress events are emitted correctly."""
    progress_callback = AsyncMock()
    flow = AgenticRAGFlow(
        enable_progress_stream=False, progress_callback=progress_callback
    )

    # Mock format_progress_event to return the raw inputs as a dict for easy verification
    with patch(
        "app.orchestration.flow.format_progress_event", new_callable=AsyncMock
    ) as mock_fmt:
        mock_fmt.side_effect = lambda t, s, m, d=None: {
            "type": t,
            "status": s,
            "msg": m,
        }

        await flow._emit_progress("test_type", "in_progress", "working...")

        progress_callback.assert_called_once_with(
            {"type": "test_type", "status": "in_progress", "msg": "working..."}
        )


@pytest.mark.asyncio
async def test_determine_retrieval_strategy_flow(mock_dependencies):
    """Test the retrieval strategy step including parsing and state updates."""
    flow = AgenticRAGFlow()
    flow.state.query = "test query"

    # Mock crew kickoff return
    mock_crew = mock_dependencies["crews"].retrieval_strategy_crew.return_value
    mock_crew.kickoff_async = AsyncMock(
        return_value='{"strategy": "hybrid", "query_rewrite": "rewrite", "assistant": "soliq"}'
    )

    await flow.determine_retrieval_strategy()

    # Verify state updates
    assert flow.state.selected_assistant == "soliq"
    assert flow.state.retrieval_output["strategy"] == "hybrid"
    assert flow.state.retrieval_output["query_rewrite"] == "rewrite"


@pytest.mark.asyncio
async def test_fetch_documents_flow(mock_dependencies):
    """Test document retrieval step."""
    flow = AgenticRAGFlow()
    flow.state.query = "test query"
    flow.state.retrieval_output = {
        "strategy": "hybrid",
        "query_rewrite": "rewrite",
        "assistant": "umumiy",
        "query_translations": {"en": "rewrite_en"},
    }

    # Mock retrieval service
    mock_retrieval = mock_dependencies["chain"].retrieval_service
    mock_retrieval.retrieve_multilingual = AsyncMock(return_value=["doc1", "doc2"])
    mock_retrieval._format_results = AsyncMock(return_value="Formatted content")

    await flow.fetch_documents()

    assert flow.state.retrieval_docs == "Formatted content"

    # Verify retrieve_multilingual call
    mock_retrieval.retrieve_multilingual.assert_called_once()
    kwargs = mock_retrieval.retrieve_multilingual.call_args[1]
    assert kwargs["query_translations"] == {"en": "rewrite_en"}
    assert kwargs["search_type"] == "hybrid"


@pytest.mark.asyncio
async def test_generate_final_answer_no_stream(mock_dependencies):
    """Test answer generation without streaming."""
    flow = AgenticRAGFlow(enable_progress_stream=False)
    flow.state.query = "query"
    flow.state.resolved_query = "resolved"
    flow.state.retrieval_docs = "ctx"
    flow.state.memory_docs = "mem"

    # Mock chain returns a simple string (or tuple)
    mock_dependencies["chain"].run = AsyncMock(return_value="Final Answer")

    answer = await flow.generate_final_answer()

    assert answer == "Final Answer"
    assert flow.state.answer == "Final Answer"

    # Verify stream=False
    kwargs = mock_dependencies["chain"].run.call_args[1]
    assert kwargs["stream"] is False


@pytest.mark.asyncio
async def test_generate_final_answer_with_project_id(mock_dependencies):
    """Test answer generation logic when project_id is present (different prompt handling)."""
    flow = AgenticRAGFlow(enable_progress_stream=False)
    flow.state.query = "query"
    flow.state.resolved_query = "resolved"
    # Document split implementation expects this format
    flow.state.retrieval_docs = "PROJECT FILES:\nproj_ctx\n\nGENERAL LAWS:\nmain_ctx"
    flow.state.memory_docs = "mem"
    flow.state.project_id = "123"

    mock_dependencies["chain"].run = AsyncMock(return_value="Answer")

    with patch("app.chains.prompts.PROJECT_FILE_PROMPT") as mock_prompt:
        mock_prompt.format.return_value = "Formatted System Prompt"

        await flow.generate_final_answer()

        # Verify custom prompt formatting was used
        mock_prompt.format.assert_called_once()
        kwargs = mock_prompt.format.call_args[1]
        assert kwargs["project_context"] == "proj_ctx"
        assert kwargs["main_context"] == "main_ctx"
