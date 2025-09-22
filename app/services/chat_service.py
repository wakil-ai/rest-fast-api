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
        llm_type: str,
        query: str, 
        top_k: int = settings.TOP_K, 
        chat_history: Optional[List[MessagePair]] = None,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Handle the question by retrieving context and generating an answer.
        
        Args:
            query (str): User's question
            top_k (int): Number of top documents to retrieve (default from settings)
            chat_history (List[MessagePair]): Previous conversation history (optional)

        Returns:
            - str: Complete answer if streaming is disabled
            - AsyncGenerator[str, None]: Streaming answer if streaming is enabled
        """
        answer = await self.chat_chain.generate_answer(
            llm_type=llm_type,
            query=query,
            top_k=top_k,
            chat_history=chat_history,
        )
        return answer
