from fastapi import APIRouter, File, Form, UploadFile, Body, HTTPException

from app.services.file_management import FileManager
from app.services.chat_history_service import ChatHistoryService

router = APIRouter(prefix="/chat/files", tags=["Files"])

file_manager = FileManager()
chat_history_service = ChatHistoryService()


@router.post("/upload", summary="Upload a file")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
):
    """
    Upload a file to be associated with a message later.
    """
    status, response = await file_manager.upload_message_file(
        file=file,
        user_id=user_id,
    )

    if status != 200:
        raise HTTPException(status_code=status, detail=response)

    return {"file_id": response.file_id}


@router.patch("/{file_id}/message", summary="Associate a file with a message")
async def associate_file_with_message(
    file_id: str,
    message_id: str = Body(..., embed=True),
):
    """
    Associate an uploaded file with a message.
    """
    try:
        chat_history_service.update_file_message_id(file_id, message_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"message": "File associated with message successfully"}


@router.get("/user/{user_id}", summary="List user's uploaded files")
async def list_user_files(user_id: str):
    """
    Retrieve a list of files uploaded by a specific user.
    """
    files = chat_history_service.get_files_by_user(user_id)
    return files
