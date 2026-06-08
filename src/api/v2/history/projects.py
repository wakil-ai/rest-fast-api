"""Legal project workspace: Mongo projects + LLM-service file ingestion."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from core.dependencies import (
    get_file_manager,
    get_llm_service_client,
    get_project_service,
)
from models.chat_history import FileUploadResponse, SessionResponse
from models.projects import (
    ProjectCreateRequest,
    ProjectFileSearchQuery,
    ProjectInstructionsResponse,
    ProjectInstructionsWriteRequest,
    ProjectResponse,
    ProjectSessionCreateRequest,
    ProjectStatus,
    ProjectUpdateRequest,
)
from utils.entitlements import ensure_can_upload_files
from utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/projects", tags=["Projects"])

project_service = get_project_service()
file_manager = get_file_manager()


def _project_to_response(doc: dict, membership_role: str | None = None) -> ProjectResponse:
    d = serialize_mongo_id(doc)
    if "stats" not in d or not isinstance(d.get("stats"), dict):
        d["stats"] = {"docs": 0, "chats": 0, "reminders": 0}
    if "settings" not in d or not isinstance(d.get("settings"), dict):
        d["settings"] = {}
    if "files" not in d:
        d["files"] = []
    if "metadata" not in d:
        d["metadata"] = {}
    if membership_role is None:
        membership_role = doc.get("membership_role")
    if membership_role is not None:
        d["membership_role"] = membership_role
    return ProjectResponse.model_validate(d)


@router.get("/user/{user_id}", response_model=list[ProjectResponse])
@handle_service_error
async def list_user_projects(
    user_id: str,
    status: ProjectStatus | None = None,
    limit: int = 50,
    skip: int = 0,
):
    rows = await project_service.list_projects(
        user_id, status=status, limit=limit, skip=skip
    )
    return [_project_to_response(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectResponse)
@handle_service_error
async def create_project(request: ProjectCreateRequest):
    doc = await project_service.create_project(request)
    return _project_to_response(doc)


@router.get("/{project_id}", response_model=ProjectResponse)
@handle_service_error
async def get_project(project_id: str, owner_id: str):
    doc = await project_service.get_project(project_id, owner_id)
    role = await project_service.get_membership_role(project_id, owner_id)
    return _project_to_response(doc, membership_role=role)


@router.patch("/{project_id}", response_model=ProjectResponse)
@handle_service_error
async def patch_project(project_id: str, body: ProjectUpdateRequest):
    doc = await project_service.update_project(project_id, body)
    return _project_to_response(doc)


@router.get(
    "/{project_id}/instructions",
    response_model=ProjectInstructionsResponse,
)
@handle_service_error
async def get_project_instructions(project_id: str, user_id: str):
    return await project_service.get_project_instructions(project_id, user_id)


@router.post(
    "/{project_id}/instructions",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectInstructionsResponse,
)
@handle_service_error
async def add_project_instructions(
    project_id: str, body: ProjectInstructionsWriteRequest
):
    return await project_service.set_project_instructions(
        project_id,
        body.user_id,
        body.instructions,
        create_only=True,
    )


@router.put(
    "/{project_id}/instructions",
    response_model=ProjectInstructionsResponse,
)
@handle_service_error
async def update_project_instructions(
    project_id: str, body: ProjectInstructionsWriteRequest
):
    return await project_service.set_project_instructions(
        project_id,
        body.user_id,
        body.instructions,
    )


@router.post(
    "/{project_id}/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
)
@handle_service_error
async def create_project_session(project_id: str, body: ProjectSessionCreateRequest):
    session = await project_service.create_session_for_project(
        project_id=project_id,
        user_id=body.user_id,
        title=body.title,
        tags=body.tags,
    )
    return SessionResponse(**serialize_mongo_id(session))


@router.get("/{project_id}/sessions", response_model=list[SessionResponse])
@handle_service_error
async def list_project_sessions(
    project_id: str,
    user_id: str,
    limit: int = 50,
    skip: int = 0,
):
    sessions = await project_service.list_project_sessions(
        project_id, user_id, limit=limit, skip=skip
    )
    return [SessionResponse(**serialize_mongo_id(s)) for s in sessions]


@router.post("/{project_id}/files", response_model=FileUploadResponse)
@handle_service_error
async def upload_project_file(
    project_id: str,
    file: UploadFile = File(...),
    user_id: str = Form(...),
    webhook_url: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
):
    # Paid-plan entitlement gate — block before any file read/processing.
    await ensure_can_upload_files(
        user_id, endpoint=f"POST /projects/{project_id}/files"
    )

    code, resp = await file_manager.upload_project_file(
        file,
        user_id=user_id,
        project_id=project_id,
        webhook_url=webhook_url,
        session_id=session_id,
    )
    if code != 200:
        raise HTTPException(code, str(resp))
    return resp


@router.get("/{project_id}/files")
@handle_service_error
async def list_project_files(project_id: str, user_id: str, limit: int = 100):
    files = await project_service.list_project_files(project_id, user_id, limit=limit)
    return [serialize_mongo_id(f) for f in files]


@router.delete(
    "/{project_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_service_error
async def delete_project_file(project_id: str, file_id: str, user_id: str):
    code, detail = await file_manager.delete_project_file(
        project_id=project_id,
        file_id=file_id,
        user_id=user_id,
    )
    if code != status.HTTP_204_NO_CONTENT:
        raise HTTPException(status_code=code, detail=detail or "Failed to delete file")


@router.post("/{project_id}/search")
@handle_service_error
async def search_project_knowledge(project_id: str, body: ProjectFileSearchQuery):
    await project_service.get_project(project_id, body.user_id)
    return await get_llm_service_client().search_project(
        {
            "project_id": project_id,
            "user_id": body.user_id,
            "query": body.q,
            "top_k": body.top_k,
        }
    )
