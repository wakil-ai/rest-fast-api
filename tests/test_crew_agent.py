# somewhere in your app (e.g., a service or controller)
import sys
import asyncio
sys.path.append(".")
from app.agent.flow import run_agentic_rag


async def main():
    result = await run_agentic_rag(
        user_id="5904877504", 
        session_id="e5d84aca-4473-4da0-9b40-d45dd6296ba7",
        query="my.gov.uz sayti raqami qanday?", # Mening ismim nima?
        user_type="lawyer",
    )
    print("Agent Response:", result)
    

if __name__ == "__main__":    
    asyncio.run(main())
