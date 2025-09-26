from fastapi import APIRouter, BackgroundTasks
from fastapi import Depends, Form
from typing import Optional
from fastapi import UploadFile, File

from app.core.logger import logger
from app.models.ingest import IngestionMarkdown
from app.ingest.ingestion_service import IngestionService


router = APIRouter(prefix="/ingest", tags=["Ingestion"])

# Initialize service
ingestion_service = IngestionService()

@router.post("/markdown", summary="Ingest a markdown document")
async def ingest_markdown(file: UploadFile, 
                          doc_id: int = Form(...),
                          metadata: Optional[str] = Form(None),
                          background_tasks: BackgroundTasks = None):
    """
    Ingest a markdown document by splitting it into chunks and storing in vector DB.
    Also stores the full document in MongoDB with metadata.
    """
    content = (await file.read()).decode('utf-8')
    background_tasks.add_task(ingestion_service.ingest_markdown, content, doc_id, metadata)
    return {"status": "Ingestion started in background"}