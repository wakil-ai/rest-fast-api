from pathlib import Path

from datalab_sdk import AsyncDatalabClient, ConvertOptions

from app.core.config import settings
from app.core.logger import logger
from app.utils.tokens import count_tokens, truncate_to_token_limit


class OCRService:
    def __init__(self):
        self.options = ConvertOptions(
            output_format="markdown",  # "markdown", "html", "json", "chunks"
            mode="fast",  # "fast", "balanced", "accurate"
            paginate=True,  # Add page delimiters
            page_range="0-200",  # Process specific pages (0-indexed)
        )
        self.token_limit = settings.FILE_CONTENT_TOKEN_LIMIT
        self.client = AsyncDatalabClient(api_key=settings.DATALAB_API_KEY)

    async def process_file(self, file: Path | str) -> str:
        """
        Sends the file to Datalab
        """
        logger.debug(f"[OCR Service] Processing file: {file}")

        try:
            result = await self.client.convert(
                file_path=str(file), options=self.options
            )
            if not result.success:
                raise ValueError(f"[OCR Service] OCR conversion failed: {result.error}")
            context = result.markdown
            if count_tokens(context) > self.token_limit:
                # Truncate content to the token limit
                logger.warning(
                    f"[OCR Service] File content exceeds token limit of "
                    f"{self.token_limit} tokens. Truncating content."
                )
                context = truncate_to_token_limit(context, self.token_limit)

            logger.success("[OCR Service] Conversion successful")
            return context
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
            logger.success("[OCR Service] Conversion successful")
            return result.markdown
        except Exception as e:
            logger.error(f"[OCR Service] Conversion failed: {str(e)}")
            raise
