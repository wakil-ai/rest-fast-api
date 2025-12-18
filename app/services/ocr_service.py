from pathlib import Path
from typing import Union
from app.core.config import settings
from datalab_sdk import AsyncDatalabClient


class OCRService:
    def __init__(self):
        self.client = AsyncDatalabClient(api_key=settings.DATABLAB_API_KEY)

    async def process_file(self, file: Union[Path, str]) -> str:
        """
        Sends the file to Datalab 
        """
        result = await self.client.convert(file)
        return result.markdown
