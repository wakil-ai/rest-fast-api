import json
from typing import Any

from crewai.flow.flow import Flow, listen, router, start

from app.chains.chat_chain import ChatChain, GenerationContext
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.logger import logger
from app.orchestration.crews import Crews
from app.orchestration.schemas import (
    AgenticRAGState,
    ContextEvaluationResponse,
    MemoryAgentResponse,
    ProgressEventType,
    RetrievalStrategyResponse,
    WebSearchResponse,
)
from app.services.memory_service import ChatMemoryService
from app.utils.streaming import format_progress_event


class AgenticRAGFlow(Flow[AgenticRAGState]):
    """
    Agentic RAG pipeline with memory, retrieval strategy, context evaluation, and web search.

    Flow:
        1. Memory Retrieval (session + personal context)
        2. Retrieval Strategy & Assistant Selection
        3. Document Retrieval
        4. Context Evaluation
        5. Web Search (conditional - only if context insufficient)
        6. Answer Generation
    """

    def __init__(self, enable_progress_stream: bool = False, progress_callback=None):
        super().__init__(tracing=settings.TRACING)
        self.enable_progress_stream = enable_progress_stream
        self.progress_callback = progress_callback
        self._initialize_services()
        self._initialize_crews()

    def _initialize_services(self) -> None:
        """Initialize all required services."""
        self.chat_chain = ChatChain()
        self.memory_service = ChatMemoryService()

    def _initialize_crews(self) -> None:
        """Initialize and cache all crews once."""
        crews_factory = Crews()
        self.memory_crew = crews_factory.memory_crew()
        self.retrieval_strategy_crew = crews_factory.retrieval_strategy_crew()
        self.evaluation_crew = crews_factory.evaluation_crew()
        self.web_search_crew = crews_factory.web_search_crew()

    # Progress Management
    async def _emit_progress(
        self, event_type: str, status: str, message: str, details: dict = None
    ) -> None:
        """Emit progress event if callback is registered."""
        if self.progress_callback:
            event = await format_progress_event(event_type, status, message, details)
            await self.progress_callback(event)

    # Flow Steps
    @start()
    async def start_memory_retrieval(self) -> None:
        """Retrieve and summarize session and personal memory."""
        await self._emit_progress(
            ProgressEventType.MEMORY_RETRIEVAL,
            "in_progress",
            "Analyzing your conversation history and preferences...",
        )

        try:
            session_memory, personal_memory = await self._fetch_memories()

            if not await self._has_any_memory(session_memory, personal_memory):
                logger.info("No memories found; skipping summarization.")
                await self._set_default_memory()
                await self._emit_progress(
                    ProgressEventType.MEMORY_RETRIEVAL,
                    "completed",
                    "Memory context loaded",
                )
                return

            memory_response = await self._summarize_memory(
                session_memory, personal_memory
            )
            await self._apply_memory_response(memory_response)

            self.state.enriched_query = await self._enrich_query_with_memory(
                self.state.query
            )

            # Notify if query was enriched
            if (
                self.state.enriched_query
                and self.state.enriched_query != self.state.query
            ):
                await self._emit_progress(
                    ProgressEventType.MEMORY_RETRIEVAL,
                    "in_progress",
                    "Query enriched with conversation context for better retrieval",
                )

            await self._emit_progress(
                ProgressEventType.MEMORY_RETRIEVAL,
                "completed",
                "Memory context loaded successfully",
            )

        except Exception as error:
            await self._handle_error("Memory retrieval", error)
            await self._set_default_memory()
            await self._emit_progress(
                ProgressEventType.MEMORY_RETRIEVAL,
                "completed",
                "Proceeding without memory context",
            )

    @listen(start_memory_retrieval)
    async def determine_retrieval_strategy(self) -> None:
        """Analyze query to select optimal retrieval strategy."""
        await self._emit_progress(
            ProgressEventType.RETRIEVAL_STRATEGY,
            "in_progress",
            "Determining best search strategy for your question...",
        )

        try:
            query_input = {"query": self.state.enriched_query or self.state.query}
            result = await self.retrieval_strategy_crew.kickoff_async(
                inputs=query_input,
            )
            strategy_response = await self._parse_structured_output(
                result, RetrievalStrategyResponse
            )

            self.state.retrieval_output = strategy_response.model_dump()
            self.state.selected_assistant = strategy_response.assistant

            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                f"Using {strategy_response.assistant} assistant",
            )

        except Exception as error:
            await self._handle_error("Strategy selection", error)
            await self._set_default_retrieval_strategy()
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                "Using default search strategy",
            )

    @listen(determine_retrieval_strategy)
    async def fetch_documents(self) -> None:
        """Execute document retrieval using selected strategy and assistant translations."""
        await self._emit_progress(
            ProgressEventType.DOCUMENT_RETRIEVAL,
            "in_progress",
            "Searching legal document database across multiple languages...",
        )

        try:
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            assistant = self.state.selected_assistant or "umumiy"
            self.state.rewritten_query = self.state.retrieval_output.get(
                "query_rewrite", self.state.query
            )

            # Get multilingual translations from agent output
            query_translations = self.state.retrieval_output.get(
                "query_translations", {}
            )

            # Fallback to rewritten query if translations are missing
            if not query_translations:
                query_translations = {"original": self.state.rewritten_query}

            # Get collection name from assistant configuration
            collection_name = self._get_collection_name(assistant, strategy)

            # Main context with multilingual support
            self.state.retrieval_docs = await self._retrieve_standard_documents(
                query_translations, strategy, collection_name
            )

            # Retrieve additional user uploaded documents
            if self.state.project_id:
                project_context = await self._retrieve_project_documents()
                self.state.retrieval_docs += "\n\nPROJECT FILES:\n" + project_context

            if self.state.file_ids:
                file_context = await self._get_file_id_context()
                self.state.retrieval_docs += file_context

            await self._emit_progress(
                ProgressEventType.DOCUMENT_RETRIEVAL,
                "completed",
                "Retrieved relevant documents from knowledge base",
            )

        except Exception as error:
            await self._handle_error("Document retrieval", error)
            self.state.retrieval_docs = ""

    @router(fetch_documents)
    async def evaluate_context_sufficiency(self) -> str:
        """Evaluate if retrieved context is sufficient to answer the query."""
        await self._emit_progress(
            ProgressEventType.CONTEXT_EVALUATION,
            "in_progress",
            "Evaluating document relevance and quality...",
        )

        try:
            evaluation_input = {
                "query": self.state.query,
                "context": self.state.retrieval_docs or "No context retrieved.",
            }

            result = await self.evaluation_crew.kickoff_async(
                inputs=evaluation_input,
            )

            evaluation_response = await self._parse_structured_output(
                result, ContextEvaluationResponse
            )
            self.state.context_evaluation_output = evaluation_response.model_dump()

            return "sufficient" if evaluation_response.is_sufficient else "insufficient"

        except Exception as error:
            await self._handle_error("Context evaluation", error)
            self.state.context_evaluation_output = {
                "is_sufficient": True,
                "reasoning": "Evaluation failed, proceeding with available context",
                "missing_info": "",
            }
            await self._emit_progress(
                ProgressEventType.CONTEXT_EVALUATION,
                "completed",
                "Proceeding with available information",
            )
            return "sufficient"

    @listen("insufficient")
    async def perform_web_search(self) -> None:
        """Perform web search when context is insufficient."""
        await self._emit_progress(
            ProgressEventType.CONTEXT_EVALUATION,
            "in_progress",
            "Switching to web search for additional information...",
        )

        await self._emit_progress(
            ProgressEventType.WEB_SEARCH,
            "in_progress",
            "Searching the web for additional information...",
        )

        try:
            web_response = await self._execute_web_search()
            await self._merge_web_documents(web_response)

            message = await self._format_web_search_message(web_response)
            await self._emit_progress(
                ProgressEventType.WEB_SEARCH, "completed", message
            )

        except Exception as error:
            await self._handle_error("Web search", error)
            await self._emit_progress(
                ProgressEventType.WEB_SEARCH,
                "completed",
                "Proceeding without web results",
            )

    @listen(perform_web_search)
    async def generate_final_answer(self) -> str:
        """Generate comprehensive answer from all retrieved context."""
        return await self._generate_answer()

    @listen("sufficient")
    async def generate_final_answer_sufficient(self) -> str:
        """Generate answer when context is sufficient."""
        return await self._generate_answer()

    # Memory Operations
    async def _fetch_memories(self) -> tuple[Any, dict]:
        """Fetch session and personal memories concurrently."""
        session_memory = await self.memory_service.get_session_memory(
            user_id=self.state.user_id, session_id=self.state.session_id
        )
        personal_memory = await self.memory_service.get_all_memories(
            user_id=self.state.user_id
        )
        return session_memory, personal_memory

    async def _has_any_memory(self, session_memory: Any, personal_memory: dict) -> bool:
        """Check if any memory exists."""
        has_session = bool(session_memory)
        has_personal = bool(personal_memory and personal_memory.get("memories"))
        return has_session or has_personal

    async def _summarize_memory(
        self, session_memory: Any, personal_memory: dict
    ) -> MemoryAgentResponse:
        """Summarize memory using cached agent."""
        memory_input = {
            "query": self.state.query,
            "session_memory": json.dumps(session_memory, ensure_ascii=False),
            "personal_memory": json.dumps(personal_memory, ensure_ascii=False),
        }
        result = await self.memory_crew.kickoff_async(inputs=memory_input)
        return await self._parse_structured_output(result, MemoryAgentResponse)

    async def _apply_memory_response(
        self, memory_response: MemoryAgentResponse
    ) -> None:
        """Apply memory response to state."""
        self.state.memory_output = memory_response.model_dump()
        self.state.memory_docs = await self._format_memory_context(memory_response)
        self.state.resolved_query = memory_response.resolved_query or self.state.query

    async def _set_default_memory(self) -> None:
        """Set default empty memory."""
        default_response = MemoryAgentResponse(
            session_notes="", personal_notes="", resolved_query=self.state.query
        )
        self.state.memory_output = default_response.model_dump()
        self.state.memory_docs = ""
        self.state.resolved_query = self.state.query

    async def _format_memory_context(self, memory_response: MemoryAgentResponse) -> str:
        """Format memory response for context inclusion."""
        return f"""
            Session Context:
            {memory_response.session_notes}

            User Preferences:
            {memory_response.personal_notes}
        """.strip()

    async def _enrich_query_with_memory(self, base_query: str) -> str:
        """Enrich query with resolved memory context if available."""
        if not self._should_enrich_query():
            return base_query
        return f"{base_query} Resolved Query from memory: {self.state.resolved_query}".strip()

    def _should_enrich_query(self) -> bool:
        """Check if query should be enriched with memory context."""
        return (
            self.state.resolved_query and self.state.resolved_query != self.state.query
        )

    # Document Retrieval
    def _get_collection_name(self, assistant: str, strategy: str) -> str:
        """Get collection name based on assistant and strategy."""
        try:
            collection_name = AssistantConfig.get_collection_name(assistant)
        except ValueError:
            collection_name = settings.MILVUS_MAIN_NAME

        # If specific strategy, always use main collection
        if strategy == "specific":
            collection_name = settings.MILVUS_MAIN_NAME

        return collection_name

    async def _retrieve_project_documents(self) -> str:
        """Retrieve documents for project context (dual retrieval)."""
        # Project-specific context
        project_context = (
            await self.chat_chain.retrieval_service.retrieve_project_context(
                query=self.state.rewritten_query,
                project_id=self.state.project_id,
                user_id=self.state.user_id,
                top_k=settings.TOP_K // 2,
            )
        )

        return project_context

    async def _retrieve_standard_documents(
        self, query_translations: dict, strategy: str, collection_name: str
    ) -> str:
        """Retrieve standard documents."""
        documents_list = await self.chat_chain.retrieval_service.retrieve_multilingual(
            query_translations=query_translations,
            top_k=settings.TOP_K,
            search_type=strategy,
            collection_name=collection_name,
        )

        return await self.chat_chain.retrieval_service._format_results(documents_list)

    async def _get_file_id_context(self) -> str:
        """Retrieve documents for provided file IDs."""
        file_context = ""
        for file_id in self.state.file_ids:
            file = self.chat_chain.chat_history_service.get_file_by_id(file_id)
            if file:
                file_context += f"\n\nFile: {file['file_metadata']['file_name']}\n{file['ocr_result']}"

        return file_context

    async def _set_default_retrieval_strategy(self) -> None:
        """Set default retrieval strategy."""
        default_response = RetrievalStrategyResponse(
            strategy="hybrid", query_rewrite=self.state.query, assistant="umumiy"
        )
        self.state.retrieval_output = default_response.model_dump()
        self.state.selected_assistant = "umumiy"

    # Web Search
    async def _execute_web_search(self) -> WebSearchResponse:
        """Execute web search using web search crew."""
        inputs = {"query": self.state.rewritten_query}
        result = await self.web_search_crew.kickoff_async(inputs=inputs)
        web_response = await self._parse_structured_output(result, WebSearchResponse)
        self.state.web_search_output = web_response.model_dump()
        return web_response

    async def _merge_web_documents(self, web_response: WebSearchResponse) -> None:
        """Merge web documents into retrieval context."""
        combined_docs = self.state.retrieval_docs or ""
        combined_docs += "\n\n--- Web Search Results ---\n"

        for doc in web_response.docs:
            combined_docs += f"\nSource: {doc.url}\n"
            if doc.title:
                combined_docs += f"Title: {doc.title}\n"
            combined_docs += f"{doc.content}\n"

        self.state.retrieval_docs = combined_docs

    async def _format_web_search_message(self, web_response: WebSearchResponse) -> str:
        """Format web search results message."""
        docs = web_response.docs
        if not docs:
            return "No relevant web documents found"

        sources = [
            f"Title: {doc.title}\n Url:({doc.url})\n Content: ({doc.content})\n\n"
            for doc in docs
        ]
        sources_str = "; ".join(sources)

        return f"Found {len(docs)} web document(s) as additional sources: {sources_str}"

    # Answer Generation
    async def _generate_answer(self) -> str:
        """Generate comprehensive answer from all retrieved context."""
        await self._emit_progress(
            ProgressEventType.ANSWER_GENERATION,
            "in_progress",
            "Generating final answer...",
        )

        try:
            # Build enhanced query
            query = self._build_enhanced_query()

            # Build system prompt
            system_prompt = self._build_system_prompt()

            # Get LLM
            llm = self.chat_chain._get_llm(self.state.llm_model)

            # Generate response
            debug_data = {"retrieval_docs": self.state.retrieval_docs}

            # Handle streaming vs non-streaming
            if self.enable_progress_stream:
                response = self.chat_chain._generate_stream(
                    llm=llm,
                    query=query,
                    gen_context=self._create_chat_context(system_prompt, debug_data),
                )
                return await self._handle_streaming_response(response)
            else:
                response = await self.chat_chain._generate_non_stream(
                    llm=llm,
                    query=query,
                    gen_context=self._create_chat_context(system_prompt, debug_data),
                )
                return self._handle_non_streaming_response(response)

        except Exception as error:
            await self._handle_error("Answer generation", error)
            self.state.answer = "Error generating answer. Please try again."
            await self._emit_progress(
                ProgressEventType.ANSWER_GENERATION,
                "failed",
                f"Answer generation failed: {str(error)}",
            )
            return self.state.answer

    def _build_enhanced_query(self) -> str:
        """Build query with resolved context."""
        base_query = self.state.query
        if self.state.resolved_query and self.state.resolved_query != base_query:
            return f"{base_query}\nResolved Query which may help you to clarify question: {self.state.resolved_query}"
        return base_query

    def _build_system_prompt(self) -> str:
        """Build system prompt based on context type."""
        if self.state.project_id:
            return self._build_project_system_prompt()
        else:
            return self._build_standard_system_prompt()

    def _build_project_system_prompt(self) -> str:
        """Build system prompt for project context."""
        parts = self.state.retrieval_docs.split("\n\nGENERAL LAWS:\n")
        project_ctx = parts[0].replace("PROJECT FILES:\n", "")
        main_ctx = parts[1] if len(parts) > 1 else ""

        template = self._get_assistant_prompt_template("project_file")

        return template.format(
            project_context=project_ctx,
            main_context=main_ctx,
            chat_history=self.state.memory_docs,
        )

    def _build_standard_system_prompt(self) -> str:
        """Build standard system prompt."""
        prompt_template = self._get_assistant_prompt_template(
            self.state.selected_assistant
        )

        return prompt_template.format(
            context=self.state.retrieval_docs,
            chat_history=self.state.memory_docs,
        )

    def _get_assistant_prompt_template(self, assistant_name: str) -> str:
        """Get prompt template for a specific assistant."""
        return AssistantConfig.get_assistant_prompt_template(assistant_name)

    def _create_chat_context(self, system_prompt: str, debug_data: dict) -> Any:
        """Create GenerationContext object for chat chain."""
        return GenerationContext(
            context=self.state.retrieval_docs,
            system_prompt=system_prompt,
            chat_history=self.state.memory_docs,
            debug_data=debug_data,
        )

    async def _handle_streaming_response(self, response) -> str:
        """Handle streaming response and emit chunks."""
        full_answer = ""
        async for chunk in response:
            if isinstance(chunk, dict):
                continue

            full_answer += chunk
            await self._emit_progress("chunk", "in_progress", chunk)

        self.state.answer = full_answer
        return full_answer

    def _handle_non_streaming_response(self, response) -> str:
        """Handle non-streaming response."""
        if isinstance(response, tuple):
            answer, _ = response
            self.state.answer = answer
            return answer
        else:
            self.state.answer = response
            return response

    # Utilities
    async def _parse_structured_output(self, output: Any, schema_class: type) -> Any:
        """Parse structured output using Pydantic schema."""
        output_str = output.raw if hasattr(output, "raw") else str(output)
        parsed_dict = await self._parse_json_output(output_str)

        try:
            return schema_class(**parsed_dict)
        except Exception as error:
            logger.warning(f"Failed to parse into {schema_class.__name__}: {error}")
            return schema_class()

    async def _parse_json_output(self, output_str: str) -> dict[str, Any]:
        """Parse JSON from string with fallback handling."""
        output_str = output_str.replace(": None", ": null")

        if parsed := await self._try_direct_json_parse(output_str):
            return parsed

        if parsed := await self._try_extract_code_block(output_str):
            return parsed

        logger.warning(f"Could not parse JSON from output: {output_str[:200]}...")
        return {}

    async def _try_direct_json_parse(self, text: str) -> dict[str, Any] | None:
        """Attempt direct JSON parsing."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def _try_extract_code_block(self, text: str) -> dict[str, Any] | None:
        """Extract and parse JSON from markdown code blocks."""
        for delimiter in ["```json", "```"]:
            if delimiter not in text:
                continue

            try:
                json_str = text.split(delimiter)[1].split("```")[0].strip()
                return json.loads(json_str)
            except (IndexError, json.JSONDecodeError):
                continue

        return None

    async def _handle_error(self, operation: str, error: Exception) -> None:
        """Log error and add to state."""
        logger.error(f"✗ {operation} failed: {error}", exc_info=True)
        self.state.errors.append(f"{operation} error: {str(error)}")
