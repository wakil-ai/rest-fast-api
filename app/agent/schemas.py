# app/agent/schemas.py
from pydantic import BaseModel
from typing import List, Optional

class MemoryOut(BaseModel):
    session_notes: Optional[str] = ""
    personal_notes: Optional[str] = ""
    # free-form stitched memory for prompt building
    merged_memory: Optional[str] = ""

class RetrievedDoc(BaseModel):
    text: str
    source: Optional[str] = None   # URL or citation path if available
    score: Optional[float] = None

class RetrievalOut(BaseModel):
    query_rewrite: str
    strategy: str                  # "hybrid" | "dense" | "sparse" | "specific"
    docs: List[RetrievedDoc]
    need_web: bool                 # True if insufficient

class WebOut(BaseModel):
    urls: List[str]

class ExtractedOut(BaseModel):
    docs: List[RetrievedDoc]       # normalized to same doc shape

class SystemPromptIn(BaseModel):
    context: str
    chat_history: str
    user_type: str
    language_instruction: str

class SystemPromptOut(BaseModel):
    system_prompt: str
