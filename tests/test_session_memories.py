import asyncio
import sys

sys.path.append(".")  # Ensure current directory is in path for imports

from app.retrieval.retrieval_service import RetrievalService

async def test_get_session_memory():
    retrieval_service = RetrievalService()
    user_id = "5904877504"
    session_id = "e5d84aca-4473-4da0-9b40-d45dd6296ba7"
    
    session_memory = await retrieval_service.get_session_memory(user_id, session_id)
    
    return session_memory

if __name__ == "__main__":
    session_memory = asyncio.run(test_get_session_memory())
    print(session_memory)