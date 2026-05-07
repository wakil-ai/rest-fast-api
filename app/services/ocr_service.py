import asyncio
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("DOCLING_DEVICE", "cpu")

from datalab_sdk import AsyncDatalabClient, ConvertOptions
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)
from langchain_docling.loader import DoclingLoader, ExportType

from app.core.config import settings
from app.core.logger import logger


class OCRService:
    def __init__(self):
        self.options = ConvertOptions(
            output_format="markdown",  # "markdown", "html", "json", "chunks"
            mode="fast",  # "fast", "balanced", "accurate"
            paginate=True,  # Add page delimiters
            page_range="0-99",  # Process the first 100 pages (0-indexed)
        )
        self.client = AsyncDatalabClient(api_key=settings.DATALAB_API_KEY)
        self.docling_converter = self._build_docling_converter()

    def _build_docling_converter(self) -> DocumentConverter:
        pipeline_options = PdfPipelineOptions(
            accelerator_options=AcceleratorOptions(
                num_threads=max(1, (os.cpu_count() or 4) // 2),
                device=AcceleratorDevice.CPU,
            ),
            do_ocr=True,
            do_table_structure=True,
        )
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            }
        )

    async def _convert_doc_to_docx(
        self, path: Path
    ) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
        libreoffice = shutil.which("libreoffice")
        if libreoffice is None:
            raise FileNotFoundError("libreoffice executable was not found on PATH")

        temp_dir = tempfile.TemporaryDirectory()
        try:
            process = await asyncio.create_subprocess_exec(
                libreoffice,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                temp_dir.name,
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")
            converted_path = Path(temp_dir.name) / f"{path.stem}.docx"
            if process.returncode != 0:
                raise RuntimeError(
                    "LibreOffice conversion failed "
                    f"(exit_code={process.returncode}, stdout={stdout!r}, "
                    f"stderr={stderr!r})"
                )
            if not converted_path.exists():
                raise FileNotFoundError(
                    "LibreOffice conversion did not produce expected DOCX "
                    f"{converted_path.name} (stdout={stdout!r}, "
                    f"stderr={stderr!r})"
                )
            return converted_path, temp_dir
        except Exception:
            temp_dir.cleanup()
            raise

    def _load_with_docling_sync(self, file: Path | str) -> str:
        loader = DoclingLoader(
            file_path=str(file),
            converter=self.docling_converter,
            export_type=ExportType.MARKDOWN,
        )
        docs = loader.load()
        return "\n\n".join(doc.page_content for doc in docs)

    async def _prepare_file_for_processing(
        self, file: Path | str
    ) -> tuple[str, tempfile.TemporaryDirectory[str] | None]:
        file_path = (
            Path(file) if not str(file).startswith(("http://", "https://")) else None
        )
        process_source = str(file)
        temp_dir: tempfile.TemporaryDirectory[str] | None = None

        if file_path is not None and file_path.suffix.lower() == ".doc":
            converted_path, temp_dir = await self._convert_doc_to_docx(file_path)
            process_source = str(converted_path)

        return process_source, temp_dir

    async def _process_with_docling(self, file: Path | str) -> str:
        load_source, temp_dir = await self._prepare_file_for_processing(file)
        try:
            return await asyncio.to_thread(self._load_with_docling_sync, load_source)
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    async def _process_file_with_datalab(self, file: Path | str) -> str:
        file_path, temp_dir = await self._prepare_file_for_processing(file)
        try:
            result = await self.client.convert(file_path=file_path, options=self.options)
            if not result.success:
                raise ValueError(f"[OCR Service] OCR conversion failed: {result.error}")
            return result.markdown
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    async def _process_url_with_datalab(self, url: str) -> str:
        result = await self.client.convert(file_url=url, options=self.options)
        if not result.success:
            raise ValueError(f"[OCR Service] OCR conversion failed: {result.error}")
        return result.markdown

    async def process_file(self, file: Path | str) -> str:
        """
        Extracts file text with Datalab first, then falls back to Docling.
        """
        logger.debug(f"[OCR Service] Processing file: {file}")

        try:
            context = await self._process_file_with_datalab(file)
            logger.success("[OCR Service] Datalab conversion successful")
            return context
        except Exception as datalab_error:
            logger.warning(
                f"[OCR Service] Datalab conversion failed, falling back to Docling: "
                f"{str(datalab_error)}"
            )

        try:
            context = await self._process_with_docling(file)
            logger.success("[OCR Service] Docling fallback conversion successful")
            return context
        except Exception as docling_error:
            logger.error(f"[OCR Service] Conversion failed: {str(docling_error)}")
            raise

    async def process_url(self, url: str) -> str:
        """
        Extracts URL text with Datalab first, then falls back to Docling.
        """
        logger.debug(f"[OCR Service] Processing URL: {url}")

        try:
            context = await self._process_url_with_datalab(url)
            logger.success("[OCR Service] Datalab URL conversion successful")
            return context
        except Exception as datalab_error:
            logger.warning(
                f"[OCR Service] Datalab URL conversion failed, falling back to Docling: "
                f"{str(datalab_error)}"
            )

        try:
            context = await self._process_with_docling(url)
            logger.success("[OCR Service] Docling URL fallback conversion successful")
            return context
        except Exception as docling_error:
            logger.error(f"[OCR Service] URL conversion failed: {str(docling_error)}")
            raise
