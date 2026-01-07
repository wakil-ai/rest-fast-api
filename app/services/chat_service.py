from collections.abc import AsyncGenerator
from typing import Any

from app.chains.chat_chain import ChatChain
from app.core.config import settings
from app.models.chat import MessagePair


class ChatService:
    """
    Service class to handle user questions and generate answers.
    """

    def __init__(self):
        self.chat_chain = ChatChain()

    async def ask_question(
        self,
        user_id: str,
        query: str,
        chat_history: list[MessagePair] | None = None,
        stream: bool = settings.STREAM,
        file_context: str | None = None,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        model_name: str | None = None,
    ) -> str | AsyncGenerator[str, None] | tuple[str, dict[str, Any]]:
        """
        Handle the question by retrieving context and generating an answer.

        Args:
            query (str): User's question
            top_k (int): Number of top documents to retrieve (default from settings)
            chat_history (List[MessagePair]): Previous conversation history (optional)
            collection_name (str): Milvus collection name to retrieve from
            model_name (str): Optional model name to use for generation

        Returns:
            - str: Complete answer if streaming is disabled and dev mode is disabled
            - Tuple[str, Dict]: Answer + debug data if streaming is disabled and dev mode is enabled
            - AsyncGenerator[str, None]: Streaming answer if streaming is enabled
        """
        answer = await self.chat_chain.generate_answer(
            user_id=user_id,
            query=query,
            chat_history=chat_history,
            stream=stream,
            file_context=file_context,
            collection_name=collection_name,
            model_name=model_name,
        )
        return answer
