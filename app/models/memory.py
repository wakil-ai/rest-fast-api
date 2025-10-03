from pydantic import BaseModel

class GetAllMemoriesResponse(BaseModel):
    user_id: str
    memories: list
    categories: list
    memory_ids: list
    
class SaveInteractionRequest(BaseModel):
    user_id: str
    query: str
    answer: str
    
    
class SaveInteractionResponse(BaseModel):
    message: str
    memories: list
    memory_ids: list
    
class UpdateMemoryRequest(BaseModel):
    text: str    

class UpdateMemoryResponse(BaseModel):
    message: str
    memory: dict