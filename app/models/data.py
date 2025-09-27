from pydantic import BaseModel

class GetDataResponse(BaseModel):
    """
    Response model for data retrieval endpoints.
    """
    results: list

class GetDataByIdResponse(BaseModel):
    """
    Response model for retrieving data by ID.
    """
    text: str

# Request model for insert endpoint
class InsertDataRequest(BaseModel):
    text: str
    metadata: dict = {}
    embedding: list = []  # Dense embedding vector
    namespace: str = "default"  # Pinecone namespace

class DeleteResponse(BaseModel):
    message: str
    deleted_id: str

class InsertResponse(BaseModel):
    message: str
    inserted_id: str