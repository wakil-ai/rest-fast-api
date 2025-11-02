import sys
sys.path.append(".")

from app.core.config import settings
api_url = "http://localhost:8080/api/chat/agent/stream"

import asyncio
import aiohttp

async def main():
    async with aiohttp.ClientSession() as session:
        payload = {
            "user_id": "5904877504",
            "session_id": "e5d84aca-4473-4da0-9b40-d45dd6296ba7",
            "query": "my.gov.uz sayti raqami qanday?",  # Mening ismim nima?
            "enable_web_search": True
        }
        
        headers = {
            "Content-Type": "application/json",
            settings.API_KEY_NAME: settings.API_KEY
        }
        
        async with session.post(api_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                print(f"Error: {resp.status}")
                return
            
            async for line in resp.content:
                if line:
                    print(line.decode('utf-8').strip())


if __name__ == "__main__":    
    asyncio.run(main())