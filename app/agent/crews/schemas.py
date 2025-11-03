from pydantic import BaseModel
from typing import List

class MemoryAgentResponse(BaseModel):
    memory_summary: str
    resolved_query: str
