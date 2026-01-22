import json
from typing import Any

from crewai.flow.flow import Flow, listen, router, start

from app.chains.chat_chain import ChatChain
from app.chains.prompts import PROMPT, SOLIQ_PROMPT
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

    async def _emit_progress(
        self, event_type: str, status: str, message: str, details: dict = None
    ) -> None:
        """Emit progress event if callback is registered."""
        if self.progress_callback:
            event = await format_progress_event(event_type, status, message, details)
            await self.progress_callback(event)

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
            # Build query input for retrieval crew
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

            # Map assistant to collection name
            collection_name = (
                settings.MILVUS_SOLIQ_ASSISTANT_NAME
                if assistant == "soliq"
                else settings.MILVUS_MAIN_NAME
            )

            # If specific strategy, always use main collection
            if strategy == "specific":
                collection_name = (
                    settings.MILVUS_MAIN_NAME
                )  # because specific means main collection

            # Check if we have project_id for dual retrieval
            project_id = self.state.project_id

            if project_id:
                # Use multilingual retrieval for main context but with half top_k
                main_docs_list = (
                    await self.chat_chain.retrieval_service.retrieve_multilingual(
                        query_translations=query_translations,
                        top_k=settings.TOP_K // 2,
                        search_type=strategy,
                        collection_name=collection_name,
                    )
                )

                # Fetch project-specific context
                # Note: retrieve_project_context currently doesn't support multilingual query_translations,
                # we use the rewritten query for now or could iterate.
                project_context = (
                    await self.chat_chain.retrieval_service.retrieve_project_context(
                        query=self.state.rewritten_query,
                        project_id=project_id,
                        user_id=self.state.user_id,
                        top_k=settings.TOP_K // 2,
                    )
                )

                main_docs_formatted = (
                    await self.chat_chain.retrieval_service._format_results(
                        main_docs_list
                    )
                )

                self.state.retrieval_docs = f"PROJECT FILES:\n{project_context}\n\nGENERAL LAWS:\n{main_docs_formatted}"
            else:
                # Standard retrieval
                documents_list = (
                    await self.chat_chain.retrieval_service.retrieve_multilingual(
                        query_translations=query_translations,
                        top_k=settings.TOP_K,
                        search_type=strategy,
                        collection_name=collection_name,
                    )
                )

                # Format documents into string for state
                self.state.retrieval_docs = (
                    await self.chat_chain.retrieval_service._format_results(
                        documents_list
                    )
                )

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
            # Build formatted input for evaluation crew
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

            # If sufficient, go to answer generation
            # If insufficient, trigger parallel retry
            return "sufficient" if evaluation_response.is_sufficient else "insufficient"

        except Exception as error:
            await self._handle_error("Context evaluation", error)
            # Default to sufficient if evaluation fails
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
        """Perform web search in parallel when context is insufficient."""
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
        await self._emit_progress(
            ProgressEventType.ANSWER_GENERATION,
            "in_progress",
            "Generating final answer...",
        )

        try:
            query = (
                self.state.query
                + "\n Resolved Query which may help you to clarify question: "
                + self.state.resolved_query
            )

            # Determine prompt template based on project_id
            from app.chains.prompts import PROJECT_FILE_PROMPT

            if self.state.project_id:
                # Extract project and main context for the PROJECT_FILE_PROMPT
                # Split the retrieval_docs back or just re-fetch for formatting if needed
                # For now, we'll assume they are combined in state.retrieval_docs
                # but it's better to pass them separately to the template if possible.
                # However, generate_answer in ChatChain already does a similar split.

                # For agentic flow, we'll manually format it to match what the prompt expects
                # since we already combined them in fetch_documents
                parts = self.state.retrieval_docs.split("\n\nGENERAL LAWS:\n")
                project_ctx = parts[0].replace("PROJECT FILES:\n", "")
                main_ctx = parts[1] if len(parts) > 1 else ""

                system_prompt = PROJECT_FILE_PROMPT.format(
                    project_context=project_ctx,
                    main_context=main_ctx,
                    chat_history=self.state.memory_docs,
                )
            else:
                system_prompt = await self.chat_chain.make_system_prompt(
                    context=self.state.retrieval_docs,
                    chat_history_text=self.state.memory_docs,
                    prompt_template=(
                        PROMPT
                        if self.state.selected_assistant == "umumiy"
                        else SOLIQ_PROMPT
                    ),
                )

            debug_data = {
                "retrieval_docs": self.state.retrieval_docs,
            }

            llm = self.chat_chain._get_llm_by_model(self.state.llm_model)

            response = await self.chat_chain.run(
                query=query,
                system_prompt=system_prompt,
                stream=True if self.enable_progress_stream else False,
                llm=llm,
                debug_data=debug_data,
            )

            if self.enable_progress_stream:
                # If streaming, we iterate over the generator and build the answer
                # while emitting chunk events
                full_answer = ""
                async for chunk in response:
                    if isinstance(chunk, dict):
                        continue

                    full_answer += chunk
                    await self._emit_progress("chunk", "in_progress", chunk)
                self.state.answer = full_answer
                return full_answer
            else:
                if isinstance(response, tuple):
                    answer, _ = response
                    self.state.answer = answer
                    return answer
                else:
                    self.state.answer = response
                    return response

        except Exception as error:
            await self._handle_error("Answer generation", error)
            self.state.answer = "Error generating answer. Please try again."
            await self._emit_progress(
                ProgressEventType.ANSWER_GENERATION,
                "failed",
                f"Answer generation failed: {str(error)}",
            )

        return self.state.answer

    @listen("sufficient")
    async def generate_final_answer_sufficient(self) -> str:
        """Generate answer when context is sufficient."""
        return await self.generate_final_answer()

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
        memory_input = await self._build_memory_input(session_memory, personal_memory)
        result = await self.memory_crew.kickoff_async(
            inputs=memory_input,
        )
        return await self._parse_structured_output(result, MemoryAgentResponse)

    async def _build_memory_input(
        self, session_memory: Any, personal_memory: dict
    ) -> dict:
        """Build input for memory crew."""
        return {
            "query": self.state.query,
            "session_memory": json.dumps(session_memory, ensure_ascii=False),
            "personal_memory": json.dumps(personal_memory, ensure_ascii=False),
        }

    async def _apply_memory_response(
        self, memory_response: MemoryAgentResponse
    ) -> None:
        """Apply memory response to state."""
        self.state.memory_output = memory_response.model_dump()
        self.state.memory_docs = await self._format_memory_context(memory_response)
        # Use original query if resolved_query is None
        self.state.resolved_query = memory_response.resolved_query or self.state.query

    async def _execute_web_search(self) -> WebSearchResponse:
        """Execute web search using web search crew and return structured response."""
        inputs = {"query": self.state.rewritten_query}
        result = await self.web_search_crew.kickoff_async(
            inputs=inputs,
        )
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
        docs = web_response.docs
        if not docs:
            return "No relevant web documents found"

        sources = []
        for doc in docs:
            sources.append(
                f"Title: {doc.title}\n Url:({doc.url})\n Content: ({doc.content})\n\n"
            )

        sources_str = "; ".join(sources)

        return (
            f"Found {len(docs)} web document(s) as additional sources: "
            f"{sources_str}"
        )

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
        # Replace None/null with proper JSON null first
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

    async def _format_memory_context(self, memory_response: MemoryAgentResponse) -> str:
        """Format memory response for context inclusion."""
        return f"""
            Session Context:
            {memory_response.session_notes}

            User Preferences:
            {memory_response.personal_notes}
        """.strip()

    async def _set_default_memory(self) -> None:
        """Set default empty memory."""
        default_response = MemoryAgentResponse(
            session_notes="", personal_notes="", resolved_query=self.state.query
        )
        self.state.memory_output = default_response.model_dump()
        self.state.memory_docs = ""
        self.state.resolved_query = self.state.query

    async def _set_default_retrieval_strategy(self) -> None:
        """Set default retrieval strategy."""
        default_response = RetrievalStrategyResponse(
            strategy="hybrid", query_rewrite=self.state.query, assistant="umumiy"
        )
        self.state.retrieval_output = default_response.model_dump()
        self.state.selected_assistant = "umumiy"

    async def _handle_error(self, operation: str, error: Exception) -> None:
        """Log error and add to state."""
        logger.error(f"✗ {operation} failed: {error}", exc_info=True)
        self.state.errors.append(f"{operation} error: {str(error)}")

    async def _enrich_query_with_memory(self, base_query: str) -> str:
        """Enrich query with resolved memory context if available."""
        if not await self._should_enrich_query():
            return base_query

        return f"{base_query} Resolved Query from memory: {self.state.resolved_query}".strip()

    async def _should_enrich_query(self) -> bool:
        """Check if query should be enriched with memory context."""
        return (
            self.state.resolved_query and self.state.resolved_query != self.state.query
        )
