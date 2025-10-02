import tiktoken
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/count", tags=["Count"])

class CountTokenRequest(BaseModel):
    text: str

token_counter = tiktoken.get_encoding("cl100k_base")

@router.post("/tokens", summary="Count tokens in text")
async def count_tokens(request: CountTokenRequest) -> int:
    tokens = token_counter.encode(request.text)
    return len(tokens)