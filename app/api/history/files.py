# app/routers/history/files.py
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile, status

from app.core.dependencies import (
    get_chat_history_service,
    get_file_manager,
    get_storage_service,
)
from app.core.logger import logger
from app.models.chat_history import FileUploadResponse
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/files", tags=["Files"])

chat_history_service = get_chat_history_service()
file_manager = get_file_manager()
storage_service = get_storage_service()


@router.get("/user/{user_id}", response_model=list[FileUploadResponse])
@handle_service_error
async def list_user_files(user_id: str, limit: int = 100):
    files = await chat_history_service.get_files_by_user(user_id=user_id, limit=limit)
    return [serialize_mongo_id(f) for f in files]


@router.post(
    "/messages",
    response_model=FileUploadResponse,
    summary="Upload file for future message",
)
async def upload_file_for_message(
    file: UploadFile = File(...),
    user_id: str = Form(...),
):
    status, resp = await file_manager.upload_message_file(file, user_id)
    if status != 200:
        raise HTTPException(status, resp)
    return resp


@router.patch("/{file_id}/message")
async def link_file_to_message(file_id: str, message_id: str = Body(..., embed=True)):
    try:
        await chat_history_service.update_file_message_id(file_id, message_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"message": "File linked to message"}


@router.get("/{file_id}", response_model=FileUploadResponse)
@handle_service_error
async def get_file(file_id: str):
    file = await chat_history_service.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, f"File {file_id} not found")
    return serialize_mongo_id(file)


@router.get("/{file_id}/view")
@handle_service_error
async def get_file_view_url(file_id: str, expiration_minutes: int = 60):
    file = await chat_history_service.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, "File not found")

    gcs_path = file.get("file_metadata", {}).get("gcs_path")
    if not gcs_path:
        raise HTTPException(500, "File path missing in metadata")

    signed_url = storage_service.get_signed_url(gcs_path, expiration_minutes)
    return {
        "file_id": file_id,
        "file_name": file.get("file_metadata", {}).get("file_name"),
        "view_url": signed_url,
        "expires_in_minutes": expiration_minutes,
        "content_type": file.get("file_metadata", {}).get("file_type"),
    }


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def delete_file(file_id: str):
    file = await chat_history_service.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, "File not found")

    gcs_path = file.get("file_metadata", {}).get("gcs_path")
    if gcs_path:
        try:
            storage_service.delete_file(gcs_path)
        except Exception as e:
            logger.warning(f"Could not delete GCS file {gcs_path}: {e}")

    await chat_history_service.delete_file_upload(file_id)
