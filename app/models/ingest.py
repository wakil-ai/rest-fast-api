from typing import Optional, Dict
from dataclasses import dataclass
from pydantic import BaseModel

@dataclass
class ReleaseData:
    date: Optional[str]
    document_number: Optional[str]
    location: Optional[str]
    
class IngestionMarkdown(BaseModel):
    doc_id: int
    metadata: Optional[Dict[str, str]] = None