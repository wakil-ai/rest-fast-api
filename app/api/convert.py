# app/api/convert.py
import asyncio
from pathlib import Path
from fastapi import APIRouter
from app.models.convert_models import ConvertReq, ConvertResp, ConvertItem
from app.utils.converter_utils import _convert_one, _iter_inputs

router = APIRouter(prefix="/convert", tags=["MD Conversion"])

@router.post("/doc-to-md", response_model=ConvertResp, summary="Convert Word-exported HTML docs to Markdown (IDs on all headings)")
async def convert_doc_to_md(req: ConvertReq):
    src = Path(req.src_dir)
    out_dir = Path(req.out_dir)
    files = await asyncio.to_thread(_iter_inputs, src)
    if not files:
        return ConvertResp(converted=0, total=0, items=[])

    sem = asyncio.Semaphore(req.concurrency)

    async def run_one(p: Path):
        async with sem:
            name, ok, note = await _convert_one(p, out_dir)
            return ConvertItem(file=name, ok=ok, note=note)

    items = await asyncio.gather(*(run_one(p) for p in files))
    converted = sum(1 for i in items if i.ok)
    return ConvertResp(converted=converted, total=len(items), items=items)