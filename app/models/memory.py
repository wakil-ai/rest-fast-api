from pydantic import BaseModel
from typing import List

class GetAllMemoriesResponse(BaseModel):
    user_id: str
    memories: list
    categories: list
    memory_ids: list
    
class InteractionsPair(BaseModel):
    query: str
    answer: str
    
class SaveInteractionRequest(BaseModel):
    user_id: str
    messages: List[InteractionsPair]
    
class SaveInteractionResponse(BaseModel):
    message: str
    
class UpdateMemoryRequest(BaseModel):
    text: str    

class UpdateMemoryResponse(BaseModel):
    message: str
    memory: dict