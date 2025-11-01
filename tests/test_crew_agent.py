# somewhere in your app (e.g., a service or controller)
import sys
import asyncio
sys.path.append(".")
from app.agent.crew import run_legal_qa_flow


async def main():
    result = await run_legal_qa_flow(
        user_id="5904877504",
        session_id="e5d84aca-4473-4da0-9b40-d45dd6296ba7",
        query="Soliq to'lash o'z vaqtidan o'tib ketsa nima bo'ladi?",
        user_type="lawyer",
    )
    print("Agent Response:", result)
    

if __name__ == "__main__":    
    asyncio.run(main())
