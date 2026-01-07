from pydantic import BaseModel


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
    messages: list[InteractionsPair]


class SaveInteractionResponse(BaseModel):
    message: str


class UpdateMemoryRequest(BaseModel):
    text: str


class UpdateMemoryResponse(BaseModel):
    message: str
    memory: dict
