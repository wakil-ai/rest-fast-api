import asyncio
import json
import sys

import aiohttp

sys.path.append(".")
from app.core.config import settings

api_url = "http://localhost:8085/api/chat/agent/stream"


async def main():
    async with aiohttp.ClientSession() as session:
        payload = {
            "user_id": "5904877504",
            "session_id": "e5d84aca-4473-4da0-9b40-d45dd6296ba7",
            "query": "my.gov.uz sayti raqami qanday?",
        }

        headers = {
            "Content-Type": "application/json",
            settings.API_KEY_NAME: settings.API_KEY,
        }

        async with session.post(api_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                print(f"Error: {resp.status}")
                print(await resp.text())
                return

            # Process the streamed response
            async for line in resp.content:
                if not line:
                    continue
                line = line.decode("utf-8").strip()

                # Server-Sent Event lines start with "data:"
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[len("data:") :].strip())
                        if data.get("type") == "chunk":
                            print(data.get("chunk"), end="", flush=True)
                        elif data.get("type") == "done":
                            print("\n--- Stream finished ---")
                            break
                    except json.JSONDecodeError:
                        # In case of malformed JSON, just ignore the line
                        continue


if __name__ == "__main__":
    asyncio.run(main())
