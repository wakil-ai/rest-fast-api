# somewhere in your app (e.g., a service or controller)
import sys
import asyncio
sys.path.append(".")
from app.chains.prompts import PROMPT
from app.agent.crew import crew


async def main():
    inputs = {
        "user_id": "6454924619",
        "session_id": "232fa88b-1440-4a7e-aa8e-5dce79533417",
        "question": "Fuqarolik kodeksining 123-moddasiga ko‘ra, tomonlarning majburiyatlari qanday?",
        "user_type": "lawyer",
        "language_instruction": "Uzbek Latin",
        "chat_history": "",
        "context": "",
        "system_policy": PROMPT.template  # or "" to use default 
    }

    result = await crew.kickoff_async(inputs=inputs)
    final_text = result["final_answer"] if isinstance(result, dict) else str(result)
    print(final_text)
    

if __name__ == "__main__":    
    asyncio.run(main())
