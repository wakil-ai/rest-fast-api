# app/services/chat_service.py

from typing import List, Optional, AsyncGenerator, Union
from app.core.config import settings
from app.chains.chat_chain import ChatChain
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
        is_lawyer: bool = False,
        chat_history: Optional[List[MessagePair]] = None,
        stream: bool = settings.STREAM,
        file_context: Optional[str] = None,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Handle the question by retrieving context and generating an answer.
        
        Args:
            query (str): User's question
            top_k (int): Number of top documents to retrieve (default from settings)
            chat_history (List[MessagePair]): Previous conversation history (optional)
            collection_name (str): Milvus collection name to retrieve from

        Returns:
            - str: Complete answer if streaming is disabled
            - AsyncGenerator[str, None]: Streaming answer if streaming is enabled
        """
        answer = await self.chat_chain.generate_answer(
            user_id=user_id,
            query=query,
            is_lawyer=is_lawyer,
            chat_history=chat_history,
            stream=stream,
            file_context=file_context,
            collection_name=collection_name,
        )
        return answer
