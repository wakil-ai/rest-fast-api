from pydantic import BaseModel, Field
from typing import List
# ---------- API models & endpoint ----------
class ConvertReq(BaseModel):
    src_dir: str = Field("data/WORD_OUTPUT", description="Folder with downloaded .doc/.htm/.html (Word HTML exports)")
    out_dir: str = Field("data/WORD_OUTPUT_MD", description="Target folder to write .md files")
    concurrency: int = Field(8, ge=1, le=32)

class ConvertItem(BaseModel):
    file: str
    ok: bool
    note: str

class ConvertResp(BaseModel):
    converted: int
    total: int