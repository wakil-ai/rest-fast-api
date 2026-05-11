import pytest
from langchain_core.messages import AIMessage

import app.agents.base as base_module
import app.agents.common.runtime as runtime_module
import app.agents.streaming as streaming_module
from app.agents import (
    AdministrativeCourtAgent,
    BaseAgent,
    CivilCourtAgent,
    ContractAnalyzerAgent,
    CourtAgent,
    CriminalCourtAgent,
    EconomicCourtAgent,
    MainAgent,
    TaxAgent,
)
from app.agents.common.state import AgentRequestContext, AgentRunResult
from app.chains.court_classifier import CourtRoutingDecision
from app.chains.chat_orchestrator import ChatOrchestrator
from app.agents.common.checkpoint import (
    init_agent_checkpointer,
    shutdown_agent_checkpointer,
)
from app.core.config import settings
from app.models.retrieval_models import RetrievalResult


def test_court_agents_are_exported_from_single_module():
    assert CourtAgent.__module__ == "app.agents.court"
    assert AdministrativeCourtAgent.__module__ == "app.agents.court"
    assert CivilCourtAgent.__module__ == "app.agents.court"
    assert CriminalCourtAgent.__module__ == "app.agents.court"
    assert EconomicCourtAgent.__module__ == "app.agents.court"


def test_chat_orchestrator_lives_in_chains_chat_orchestrator():
    assert ChatOrchestrator.__module__ == "app.chains.chat_orchestrator"


def test_chat_orchestrator_is_thin_router():
    orchestrator = ChatOrchestrator()
    assert isinstance(orchestrator.get_agent("main"), MainAgent)
    assert not hasattr(orchestrator, "prepare_generation_context_for_langgraph")
    assert not hasattr(orchestrator, "_collect_file_context")
    assert not hasattr(orchestrator, "_retrieve_relevant_context")


def test_agents_expose_graph_contract():
    for cls in (MainAgent, TaxAgent, ContractAnalyzerAgent, CourtAgent):
        agent = cls()
        assert callable(agent.build_graph)
        assert callable(agent.ainvoke)
        assert callable(agent.astream)
        assert isinstance(agent.build_graph(), tuple)


@pytest.mark.asyncio
async def test_base_agent_retrieve_preserves_file_context():
    class FakeAgent(BaseAgent):
        def __init__(self):
            super().__init__(collection_name="fake_collection", top_k=1)

        async def asearch(self, query, config):
            assert "attached context" in query
            assert config.collection_name == "fake_collection"
            return [{"text": "doc"}]

        async def format_results(self, documents):
            assert documents == [{"text": "doc"}]
            return RetrievalResult(context="retrieved", attachments=[])

    result = await FakeAgent().retrieve("question", file_context="attached context")

    assert result.context == "retrieved\n\n\nattached context"
    assert result.attachments == []


@pytest.mark.asyncio
async def test_agent_ainvoke_owns_context_and_generation(monkeypatch):
    calls = []

    class FakePrompt:
        def format(self, context, chat_history):
            return f"ctx={context}|history={chat_history}"

    class FakeRegistry:
        def get_assistant_prompt(self, assistant):
            assert assistant == "fake"
            return FakePrompt()

    class FakeAgent(BaseAgent):
        def __init__(self):
            super().__init__(collection_name="fake", assistant_name="fake")

        @property
        def prompt_registry(self):
            return FakeRegistry()

        async def load_history(self, state):
            calls.append("history")
            state.chat_history = "previous messages"

        async def load_memory(self, state):
            calls.append("memory")
            state.memory_context = "memory notes"

        async def load_file_context(self, state):
            calls.append("files")
            state.file_context = "file text"

        async def retrieve(self, **kwargs):
            calls.append("retrieve")
            assert kwargs["chat_history"] == "previous messages"
            assert kwargs["file_context"] == "file text"
            return RetrievalResult(context="retrieved law", attachments=[])

    async def fake_invoke_chat_agent(**kwargs):
        calls.append("generate")
        assert kwargs["system_prompt"] == (
            "ctx=retrieved law|history=previous messages\nmemory notes"
        )
        return "final answer", {"workflow": "fake"}

    monkeypatch.setattr(base_module, "invoke_chat_agent", fake_invoke_chat_agent)

    result = await FakeAgent().ainvoke(
        AgentRequestContext(
            user_id="u",
            session_id="s",
            message_id="m",
            query="q",
            assistant="fake",
        )
    )

    assert calls == ["history", "memory", "files", "retrieve", "generate"]
    assert result.answer == "final answer"
    assert result.resolved_assistant == "fake"


@pytest.mark.asyncio
async def test_court_agent_routes_to_concrete_agent(monkeypatch):
    class FakeClassifier:
        async def route_query(self, **kwargs):
            return CourtRoutingDecision(assistant_name="civil_court")

    class FakeCivilAgent:
        async def ainvoke(self, request):
            return AgentRunResult(
                answer="civil answer",
                resolved_assistant=request.assistant,
                metadata={"workflow": "fake"},
            )

    async def no_context(self, request):
        state = type(
            "State",
            (),
            {"chat_history": "", "file_context": "", "request": request},
        )()
        return state

    agent = CourtAgent()
    agent.court_classifier = FakeClassifier()
    agent._routed_agents["civil_court"] = FakeCivilAgent()
    monkeypatch.setattr(CourtAgent, "prepare_state_for_routing", no_context)

    result = await agent.ainvoke(
        AgentRequestContext(
            user_id="u",
            session_id="s",
            message_id="m",
            query="q",
            assistant="court",
        )
    )

    assert result.answer == "civil answer"
    assert result.resolved_assistant == "civil_court"


@pytest.mark.asyncio
async def test_agent_checkpointer_lifecycle_uses_memory_by_default(monkeypatch):
    monkeypatch.setattr(settings, "LANGGRAPH_CHECKPOINT_USE_REDIS", False)

    await init_agent_checkpointer()
    await shutdown_agent_checkpointer()


def test_iter_text_slices_for_sse_splits_large_string():
    from app.agents.common.runtime import _iter_text_slices_for_sse

    parts = list(_iter_text_slices_for_sse("a" * 500, max_chars=120))
    assert len(parts) > 1
    assert "".join(parts) == "a" * 500


@pytest.mark.asyncio
async def test_runtime_stream_emits_final_ai_message_when_no_token_chunks(monkeypatch):
    class FakeCompiledAgent:
        async def astream(self, input_state, config, stream_mode, **kwargs):
            yield {
                "type": "messages",
                "ns": (),
                "data": (
                    AIMessage(content="final answer only"),
                    {"langgraph_node": "model"},
                ),
            }

    monkeypatch.setattr(
        runtime_module,
        "_compile_lc_agent",
        lambda **kwargs: FakeCompiledAgent(),
    )
    monkeypatch.setattr(runtime_module, "_gemini_lc_model_name", lambda: "fake-model")

    items = [
        item
        async for item in runtime_module.astream_chat_agent(
            thread_id="t",
            query="q",
            system_prompt="sys",
            assistant_name="main",
        )
    ]

    assert items[0] == "final answer only"
    assert items[-1]["type"] == "_generation_meta"


@pytest.mark.asyncio
async def test_streaming_wrapper_persists_answer_and_metadata(monkeypatch):
    persisted: list[dict] = []

    class FakeOrchestrator:
        async def astream(self, request):
            yield "hel"
            yield "lo"
            yield {
                "type": "_generation_meta",
                "meta": {"model": "fake-model"},
                "resolved_assistant": "main",
            }

    class FakeChatService:
        def build_message_metadata(self, **kwargs):
            return {
                "assistant": kwargs["assistant"],
                "stream": kwargs["stream"],
                "model": kwargs["generation_meta"].get("model"),
            }

        def schedule_message_persistence(self, **kwargs):
            persisted.append(kwargs)

    monkeypatch.setattr(
        streaming_module,
        "get_chat_orchestrator",
        lambda: FakeOrchestrator(),
    )
    monkeypatch.setattr(
        streaming_module,
        "get_chat_service",
        lambda: FakeChatService(),
    )

    items = [
        item
        async for item in streaming_module.astream_chat_with_persistence(
            user_id="u",
            session_id="s",
            message_id="m",
            query="q",
            file_ids=None,
            assistant="main",
            started_at=0,
        )
    ]

    assert items[0] == {"type": "metadata", "session_id": "s", "message_id": "m"}
    assert items[1:3] == ["hel", "lo"]
    assert persisted[0]["answer"] == "hello"
    assert persisted[0]["metadata"]["model"] == "fake-model"


@pytest.mark.asyncio
async def test_streaming_wrapper_persists_partial_answer_on_error(monkeypatch):
    persisted: list[dict] = []

    class FakeOrchestrator:
        async def astream(self, request):
            yield "partial"
            raise RuntimeError("stream broke")

    class FakeChatService:
        def build_message_metadata(self, **kwargs):
            return {"assistant": kwargs["assistant"], "stream": kwargs["stream"]}

        def schedule_message_persistence(self, **kwargs):
            persisted.append(kwargs)

    monkeypatch.setattr(
        streaming_module,
        "get_chat_orchestrator",
        lambda: FakeOrchestrator(),
    )
    monkeypatch.setattr(
        streaming_module,
        "get_chat_service",
        lambda: FakeChatService(),
    )

    emitted = []
    with pytest.raises(RuntimeError):
        async for item in streaming_module.astream_chat_with_persistence(
            user_id="u",
            session_id="s",
            message_id="m",
            query="q",
            file_ids=None,
            assistant="main",
            started_at=0,
        ):
            emitted.append(item)

    assert emitted[-1] == "partial"
    assert persisted[0]["answer"] == "partial"
