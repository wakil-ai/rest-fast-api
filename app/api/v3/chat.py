"""
v3 Chat API — general assistant only, backed by LangGraph + LangChain (Gemini react agent).

Primary production entry is `/api/v2/chat/ask` with `assistant=main`, which uses the same pipeline.
This router remains as an explicit LangChain-only endpoint.
"""

from time import perf_counter

from fastapi import APIRouter, HTTPException, Request

from app.chains.general_langchain_agent import invoke_general_lc_agent
from app.chains.general_langchain_handlers import (
    astream_lc_with_persistence,
    collect_lc_file_context,
)
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import get_chat_service
from app.core.exceptions import ChatException, ChatGenerationException
from app.core.logger import logger
from app.models.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["Chat v3 (LangChain)"])

chat_service = get_chat_service()

DT_TEAM_DISCLAIMER = (
    "\n\n> *DIQQAT! Ushbu protsessual hujjat loyihasi “WakilAI” sun’iy "
    "intellekt tizimi yordamida shakllantirildi. Hujjatni sudga taqdim "
    "etishdan avval malakali va professional advokat bilan maslahatlashish "
    "tavsiya etiladi.*"
)


def _is_dt_team_request(request: Request) -> bool:
    return bool(request.headers.get(settings.DT_API_KEY_NAME.lower()))


def _append_dt_team_disclaimer(answer: str, *, is_dt_team_request: bool) -> str:
    if not is_dt_team_request:
        return answer
    return f"{answer}{DT_TEAM_DISCLAIMER}"


def _canonical_assistant(raw):
    if raw is None:
        return AssistantConfig.validate_assistant_or_default(None)

    try:
        value = getattr(raw, "value", raw)
        if isinstance(value, str):
            return AssistantConfig.validate_assistant_or_default(value)
    except Exception:
        pass

    return AssistantConfig.validate_assistant_or_default(str(raw))


def _is_general_assistant(canonical_assistant: str) -> bool:
    return canonical_assistant == "main"


@router.post(
    "/ask",
    summary="Ask (general assistant, LangChain + LangGraph + Gemini)",
    response_model=None,
)
async def ask_question(request: ChatRequest, raw_request: Request):
    canonical = _canonical_assistant(request.assistant)
    if not _is_general_assistant(canonical):
        raise HTTPException(
            status_code=400,
            detail=(
                "This endpoint accepts only the general assistant (`main`). "
                "Use `/api/v2/chat/ask` for specialized assistants."
            ),
        )

    try:
        chat_service.validate_query_length(request.query)

        credit_cost, _ = chat_service.extract_assistant_config("main")
        await chat_service.verify_user_credits(
            user_id=request.user_id,
            assistant_type="main",
            required_credits=credit_cost,
        )

        should_stream = settings.STREAM if request.stream is None else request.stream
        is_dt = _is_dt_team_request(raw_request)

        session_id, message_id = await chat_service.prepare_chat_request(
            user_id=request.user_id,
            session_id=request.session_id,
        )
        thread_id = f"{request.user_id}:{session_id}"
        fc = await collect_lc_file_context(
            user_id=request.user_id,
            query=request.query,
            file_ids=request.file_ids,
        )
        dt_suffix = DT_TEAM_DISCLAIMER if is_dt else ""

        if should_stream:
            started_stream = perf_counter()
            return chat_service.create_streaming_response(
                astream_lc_with_persistence(
                    thread_id=thread_id,
                    query=request.query,
                    file_context=fc,
                    user_id=request.user_id,
                    session_id=session_id,
                    message_id=message_id,
                    file_ids=request.file_ids,
                    assistant="main",
                    started_at=started_stream,
                    dt_team_disclaimer_suffix=dt_suffix,
                ),
            )

        started_at = perf_counter()

        try:
            answer, lc_meta = await invoke_general_lc_agent(
                thread_id=thread_id,
                query=request.query,
                file_context=fc,
                user_id_for_logs=request.user_id,
            )
        except RuntimeError as e:
            logger.error("[v3-chat] LC agent misconfiguration: %s", e, exc_info=True)
            raise ChatGenerationException(str(e)) from e

        answer_out = _append_dt_team_disclaimer(answer, is_dt_team_request=is_dt)
        latency_ms = int((perf_counter() - started_at) * 1000)

        merged_meta = dict(lc_meta or {})
        merged_meta["latency_ms"] = latency_ms

        metadata = chat_service.build_message_metadata(
            assistant="main",
            stream=False,
            latency_ms=latency_ms,
            generation_meta=merged_meta,
        )
        chat_service.schedule_message_persistence(
            user_id=request.user_id,
            session_id=session_id,
            message_id=message_id,
            query=request.query,
            answer=answer_out,
            file_ids=request.file_ids,
            metadata=metadata,
        )

        return ChatResponse(
            answer=answer_out,
            session_id=session_id,
            message_id=message_id,
            latency_ms=latency_ms,
            attachments=None,
        )

    except ChatException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ChatAPI/v3] ask_question failed: %s", e, exc_info=True)
        raise ChatGenerationException(str(e)) from e
