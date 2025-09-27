import asyncio
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException
from app.utils.lex_download_utils import (
    _process_one, UA, TIMEOUT
)
from app.models.lex_download_models import LexDownloadURLRequest, DLResponse

router = APIRouter(prefix="/lex", tags=["LEX Download"])

@router.post("/download", response_model=DLResponse)
async def download_from_urls(req: LexDownloadURLRequest):
    if not req.urls:
        raise HTTPException(status_code=400, detail="No URLs provided.")

    rows = [(str(url), str(url)) for url in req.urls]

    out_path = Path(req.out_dir)
    limits = httpx.Limits(max_connections=req.concurrency, max_keepalive_connections=req.concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=TIMEOUT, limits=limits) as client:
        sem = asyncio.Semaphore(req.concurrency)
        async def task(name, url):
            async with sem:
                return await _process_one(client, name, url, out_path, req.prefer_doc, req.delay_ms)
        results = await asyncio.gather(*(task(n, u) for n, u in rows))
    saved = sum(1 for r in results if r.saved)
    return DLResponse(saved=saved, total=len(results), results=results)