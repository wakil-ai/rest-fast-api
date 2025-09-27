from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from app.core.logger import logger
from app.db.db_manager import DBManager
from app.models.data import GetDataResponse

router = APIRouter(prefix="/data", tags=["Data"])
db_manager = DBManager()

class DeleteByUrlsRequest(BaseModel):
    urls: List[str]

@router.get("/get-all", summary="Data Endpoint for getting all data from MongoDB", response_model=GetDataResponse)
async def get_all_data():
    """
    Retrieves all data from the MongoDB collection.
    Returns:
        JSON response containing all documents in the collection.
    """
    try:
        data = db_manager.get_all_data()
        if not data:
            logger.warning("No data found in the collection.")
            return GetDataResponse(results=[])  # Return empty list instead of message
        return GetDataResponse(results=data)
    except Exception as e:
        logger.error(f"Error retrieving data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.post("/delete-by-urls", summary="Delete data by a list of URLs")
async def delete_by_urls(request: DeleteByUrlsRequest):
    """
    Deletes data from MongoDB and the vector database by a list of URLs.
    """
    try:
        result = db_manager.delete_data_by_urls(request.urls)
        return {"message": "Deletion process completed", "details": result}
    except Exception as e:
        logger.error(f"Error deleting data by URLs: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
