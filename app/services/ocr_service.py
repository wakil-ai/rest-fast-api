from pathlib import Path

from datalab_sdk import AsyncDatalabClient, ConvertOptions

from app.core.config import settings
from app.core.logger import logger


class OCRService:
    def __init__(self):
        self.options = ConvertOptions(
            output_format="chunks",  # "markdown", "html", "json", "chunks"
            mode="balanced",           # "fast", "balanced", "accurate"
            paginate=True,             # Add page delimiters
            page_range="0-10",         # Process specific pages (0-indexed)
        )
        self.client = AsyncDatalabClient(api_key=settings.DATALAB_API_KEY)

    async def process_file(self, file: Path | str) -> str:
        """
        Sends the file to Datalab
        """
        logger.debug(f"[OCR Service] Processing file: {file}")

        try:
            result = await self.client.convert(file_path=file)
            logger.success(f"[OCR Service] Conversion successful")
            return result.markdown
        except Exception as e:
            logger.error(f"[OCR Service] Conversion failed: {str(e)}")
            raise

    async def process_url(self, url: str) -> str:
        """
        Sends the URL to Datalab for processing
        """
        logger.debug(f"[OCR Service] Processing URL: {url}")

        try:
            result = await self.client.convert(file_url=url)
            logger.success(f"[OCR Service] Conversion successful")
            return result.markdown
        except Exception as e:
            logger.error(f"[OCR Service] Conversion failed: {str(e)}")
            raise