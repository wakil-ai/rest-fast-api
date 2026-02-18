from app.core.dependencies import get_fallback_llm, get_prompt_registry
from app.core.logger import logger


class MilvusQueryAgent:
    """Agent to determine the appropriate Milvus collection for a given query."""

    def __init__(self):
        self.llm = get_fallback_llm()
        self.prompt_registry = get_prompt_registry()
        self.prompt = self.prompt_registry.get_prompt("milvus_query_agent").template

    def _build_prompt(self, query, chat_history, file_context):
        return self.prompt.format(
            user_query=query,
            uploaded_file_content=file_context,
            chat_history=chat_history,
        )

    async def generate_filter(
        self, query: str, chat_history: str = "", uploaded_file_context: str = ""
    ) -> str:
        """Generate a Milvus filter expression based on the query."""
        try:
            structured_prompt = self._build_prompt(
                query, chat_history, uploaded_file_context
            )

            response = await self.llm.generate_response(
                user_prompt=query,
                system_prompt=structured_prompt,
                stream=False,
            )

            logger.debug(f"[MilvusQueryAgent] Generated filter: {response.strip()}")

            return response.strip()

        except Exception as e:
            logger.error(f"[MilvusQueryAgent] Failed to generate filter: {e}")
            return ""
