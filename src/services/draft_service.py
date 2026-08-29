"""The Non-Substitution Gate.

One service owns the whole state machine so it is written once. Cases and Tasks
reach in only for the closure guard; everything else about a draft's lifecycle
happens here.
"""

import asyncio
import hashlib
import time
from collections.abc import AsyncGenerator, Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from core.assistants import AssistantConfig
from core.config import settings
from core.dependencies import (
    get_activity_log_service,
    get_case_service,
    get_db_manager,
    get_organization_service,
    get_task_service,
    get_workflow_state_service,
)
from core.logger import logger
from models.activity_logs import ActorType, EventType
from models.drafts import DraftSource, DraftStatus
from models.projects import is_closed
from models.workflow_states import StateAppliesTo, StateCategory
from utils.message_export import (
    DRAFT_DOCX_FILENAME,
    build_markdown_docx,
    draft_markdown,
)
from utils.user_management import clean_for_mongodb, generate_short_id


# Field headings for the composed request. The employee reads and edits this
# text, so it is user-facing copy that happens to also reach the model — which
# handles all three languages, this being an Uzbek legal product.
#
# Uzbek is the fallback rather than English: an unrecognised language code on
# this product is far more likely to be a missing header than an English-
# speaking user.
DEFAULT_QUERY_LANGUAGE = "uz"

QUERY_LABELS: dict[str, dict[str, str]] = {
    "uz": {
        "title": "Sarlavha",
        "objective": "Maqsad",
        "description": "Tavsif",
        "instruction": "Ko'rsatma",
        "previous_draft": "Oldingi javob",
        "revise": (
            "Yuqoridagi javobni qayta ishlang. Nimani o'zgartirish kerakligini "
            "yozing."
        ),
    },
    "ru": {
        "title": "Заголовок",
        "objective": "Цель",
        "description": "Описание",
        "instruction": "Указание",
        "previous_draft": "Предыдущий черновик",
        "revise": "Доработайте черновик выше. Укажите, что именно изменить.",
    },
    "en": {
        "title": "Title",
        "objective": "Objective",
        "description": "Description",
        "instruction": "Instruction",
        "previous_draft": "Previous draft",
        "revise": "Revise the draft above. State what should change.",
    },
}


async def detached(coro: Coroutine[Any, Any, Any]) -> None:
    """Let a write finish even when this frame is cancelled again.

    Starlette detects a client disconnect through an anyio cancel scope, which is
    *level-triggered*: it re-raises CancelledError at every checkpoint reached
    inside the scope, including the await below. `shield` keeps the coroutine
    alive on its own task; the `except` keeps this frame from unwinding before the
    lines after it run. A bare `await asyncio.shield(...)` is not enough.

    Guarantees the write is not *cancelled*. Does NOT guarantee it has *landed* by
    the time this returns — under cancellation it returns early and the shielded
    task finishes unobserved. Never build a read-after-write on top of it.
    """
    try:
        await asyncio.shield(coro)
    except asyncio.CancelledError:
        pass


async def bounded(
    agen: AsyncGenerator[Any, None], seconds: float
) -> AsyncGenerator[Any, None]:
    """Stop a generation that runs too long, however lively it looks.

    An httpx read timeout is a per-chunk timer: it resets on every byte, so a
    stream trickling a token every few seconds never trips it. The reclaim in
    DraftService assumes a dead generation is really dead, which needs a wall
    clock as well.

    Not `asyncio.timeout()` — that is 3.11+ and this image is python:3.10-slim.
    `asyncio.wait_for` does not wrap an async generator.
    """
    deadline = time.monotonic() + seconds
    async for item in agen:
        if time.monotonic() > deadline:
            raise TimeoutError("delegation exceeded its wall clock")
        yield item


class DraftService:
    """Drafts are inert until a human decides. Nothing here bypasses that."""

    # Class-level: a constant, and tests build instances with __new__ to skip the
    # DB wiring in __init__ — an instance attribute would be missing there.
    prefix = "draft-"

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.collection = settings.DRAFTS_COLLECTION

    def _collection(self) -> Any:
        # DBManager has no .db of its own; the Motor database hangs off the
        # handler. Matches case_service.py:105. `Any` because Motor's collection
        # is an unparameterised generic — the alternative is a type error at every
        # call site for a truth the driver does not express.
        return self.db.mongo_handler.db[self.collection]

    def _orgs(self):
        return get_organization_service()

    def _cases(self):
        return get_case_service()

    def _tasks(self):
        return get_task_service()

    def _states(self):
        return get_workflow_state_service()

    def _logs(self):
        return get_activity_log_service()

    def _chat(self):
        # Imported here, not at module scope: chat_service imports this package.
        from core.dependencies import get_chat_service

        return get_chat_service()

    def _history(self):
        from core.dependencies import get_chat_history_service

        return get_chat_history_service()

    async def ensure_indexes(self) -> None:
        collection = self._collection()
        await collection.create_index(
            [("chain_id", 1), ("version", 1)], name="chain_history"
        )
        # Both listings sort by created_at descending, so it belongs in the key
        # rather than being sorted in memory. No `status` here: nothing queries
        # this collection by status — the closure guard filters on slot_active
        # and rides uniq_active_draft below.
        await collection.create_index(
            [("org_id", 1), ("case_id", 1), ("created_at", -1)],
            name="case_drafts",
        )
        # list_for_task filters on task_id with no case_id, so it cannot use the
        # index above at all — org_id would be the only usable prefix.
        await collection.create_index(
            [("org_id", 1), ("task_id", 1), ("created_at", -1)],
            name="task_drafts",
        )
        await collection.create_index([("session_id", 1)], name="session_drafts")
        # The real guarantee behind "one live draft per Case/Task". The
        # application pre-check only exists to produce a friendlier error first;
        # this is what survives a race.
        await collection.create_index(
            [("org_id", 1), ("case_id", 1), ("task_id", 1)],
            unique=True,
            partialFilterExpression={"slot_active": True},
            name="uniq_active_draft",
        )

    # The generation is bounded by AI_DELEGATION_TIMEOUT_SECONDS, but the bound can
    # only be noticed between yields — a producer that stalls just short of it and
    # then delivers one slow chunk can outlive the timeout by roughly the httpx
    # per-chunk read timeout. The margin covers the common case, not every case.
    # The real safety net is that finalize_draft's write is conditional on the row
    # still being `generating`: a reclaimed row fails that predicate, so a late
    # producer writes nothing rather than clobbering whoever took the slot next.
    RECLAIM_MARGIN_SECONDS = 120

    @property
    def RECLAIM_AFTER_SECONDS(self) -> int:
        # A property, not a class attribute: the setting must be readable at call
        # time so a deployment can change it without a rebuild.
        return settings.AI_DELEGATION_TIMEOUT_SECONDS + self.RECLAIM_MARGIN_SECONDS

    async def _reclaim_if_abandoned(
        self, org_id: str, case_id: str, task_id: str | None
    ) -> None:
        """Free a slot whose generation can no longer be alive.

        Conditional, like every other transition here: a read-then-write would
        reopen exactly the race the unique index exists to close.
        """
        held = await self._collection().find_one(
            {
                "org_id": org_id,
                "case_id": case_id,
                "task_id": task_id,
                "slot_active": True,
            }
        )
        if not held or held.get("status") != DraftStatus.generating.value:
            return
        created_at = held.get("created_at")
        if not created_at:
            return
        if created_at.tzinfo is None:
            # Mongo returns naive UTC. Comparing that to an aware `now` raises
            # TypeError, which would 500 every delegation on this Case.
            created_at = created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < created_at + timedelta(
            seconds=self.RECLAIM_AFTER_SECONDS
        ):
            return

        res = await self._collection().update_one(
            {
                "_id": held["_id"],
                "status": DraftStatus.generating.value,
                "slot_active": True,
            },
            {
                "$set": {"status": DraftStatus.failed.value},
                "$unset": {"slot_active": ""},
            },
        )
        # Only when this call is the one that freed it. The update is conditional
        # precisely because a concurrent caller may get there first, and a log
        # that fires either way would report reclaims that never happened — this
        # line is the only trace an abandoned generation leaves behind.
        if res.modified_count:
            logger.warning(
                f"[DraftService] Reclaimed abandoned draft {held['_id']} "
                f"on case {case_id}"
            )

    async def reserve_slot(
        self,
        *,
        org_id: str,
        case_id: str,
        task_id: str | None,
        session_id: str,
        assistant: str,
        user_id: str,
        query_base: str = "",
        query_final: str = "",
        query_edited: bool = False,
    ) -> str:
        """Claim the Case/Task before generating, so the loser never pays.

        Returns the new draft id. Raises 409 if someone already holds the slot —
        and that refusal happens before any credits are spent, which is the whole
        reason the row is inserted this early.
        """
        await self._reclaim_if_abandoned(org_id, case_id, task_id)

        draft_id = generate_short_id(prefix=self.prefix, type="uuid7")
        now = datetime.now(timezone.utc)
        doc = clean_for_mongodb(
            {
                "_id": draft_id,
                "org_id": org_id,
                "case_id": case_id,
                "task_id": task_id,
                "session_id": session_id,
                "message_id": None,
                "chain_id": draft_id,
                "parent_draft_id": None,
                "version": 1,
                "source": DraftSource.agent.value,
                "content": "",
                # Written with the reservation rather than at finalize: a
                # generation that fails still has to show what was asked.
                "query_base": query_base,
                "query_final": query_final,
                "query_edited": query_edited,
                "status": DraftStatus.generating.value,
                "slot_active": True,
                "assistant": assistant,
                "created_by": user_id,
                "decided_by": None,
                "decided_at": None,
                "edited": False,
                "created_at": now,
            }
        )
        try:
            # insert_one deliberately, NOT db.insert_documents: that helper calls
            # insert_many, which raises BulkWriteError instead of DuplicateKeyError
            # and would leave the 409 below unreachable.
            await self._collection().insert_one(doc)
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A draft is already awaiting confirmation",
            ) from exc
        return draft_id

    FINALIZE_ATTEMPTS = 3
    FINALIZE_BACKOFF_SECONDS = 0.2

    async def finalize_draft(
        self, draft_id: str, content: str, message_id: str
    ) -> bool:
        """Move a reserved row to `pending`. False means it was already reclaimed.

        The predicate pins `status: generating`, which is the zombie guard: a
        generation that outlived its reclaim window must not flip its own dead row
        back to decidable while a newer chain holds the slot.

        Retried because the write is idempotent — same `_id`, same predicate, no
        possibility of a duplicate — and the alternative is discarding a draft the
        customer already paid for.
        """
        for attempt in range(self.FINALIZE_ATTEMPTS):
            try:
                res = await self._collection().update_one(
                    {"_id": draft_id, "status": DraftStatus.generating.value},
                    {
                        "$set": {
                            "content": content,
                            "message_id": message_id,
                            "status": DraftStatus.pending.value,
                        }
                    },
                )
            except Exception as exc:  # any transient Mongo failure
                if attempt + 1 == self.FINALIZE_ATTEMPTS:
                    logger.error(
                        f"[DraftService] Failed to persist draft {draft_id} after "
                        f"{self.FINALIZE_ATTEMPTS} attempts: {exc}"
                    )
                    # Re-raised on the last attempt rather than stashed and thrown
                    # after the loop: the caller's `finally` releases the slot
                    # either way, and this keeps the traceback attached.
                    raise
                await asyncio.sleep(self.FINALIZE_BACKOFF_SECONDS)
                continue

            if res.matched_count == 0:
                logger.warning(
                    f"[DraftService] Draft {draft_id} was reclaimed before its "
                    "generation finished; discarding the late result"
                )
                return False
            return True

        # Unreachable: the loop either returns or raises on the final attempt.
        raise RuntimeError(f"finalize_draft fell through for {draft_id}")

    async def release_slot(self, draft_id: str) -> None:
        """Mark a draft failed and free its Case/Task for another attempt."""
        await self._collection().update_one(
            {"_id": draft_id},
            {
                "$set": {"status": DraftStatus.failed.value},
                "$unset": {"slot_active": ""},
            },
        )

    # --- delegation ---------------------------------------------------------

    def _build_query(
        self, holder: dict[str, Any], instruction: str | None, language: str = "uz"
    ) -> str:
        """The machine's opening draft of the request, composed server-side.

        This is a starting point the employee may rewrite, not a locked prompt.
        The Case context is put in front of them rather than hidden from them:
        ZRU-1115 asks for proof a human shaped the request, so the request has to
        be something a human can actually see and change. What protects the
        context is not that it cannot be edited — it is that `query_base` is
        stored alongside whatever was sent, so any divergence is on the record.

        The field labels follow the employee's own language. They are the one
        part of this text the machine writes, and a Russian lawyer reading their
        own Case under English headings is being shown a prompt rather than a
        request — which is the opposite of what the review step is for.
        """
        labels = QUERY_LABELS.get(language, QUERY_LABELS[DEFAULT_QUERY_LANGUAGE])

        parts = [f"{labels['title']}: {holder.get('title') or ''}"]
        if holder.get("objective"):
            parts.append(f"{labels['objective']}: {holder['objective']}")
        if holder.get("description"):
            parts.append(f"{labels['description']}: {holder['description']}")
        if instruction:
            parts.append(f"{labels['instruction']}: {instruction}")
        return "\n\n".join(parts)

    @staticmethod
    def _hash_query(query: str) -> str:
        """Fingerprint of a composed request, round-tripped through the client.

        Only ever compared, never trusted as input: a wrong or missing hash makes
        the request count as edited, which over-records rather than under-records.
        That is the safe direction for something whose job is to be evidence.
        """
        return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:32]

    async def _load_delegation_holder(
        self, org_id: str, case_id: str, task_id: str | None, user_id: str
    ) -> tuple[dict[str, Any], str, str]:
        """Resolve the Case or Task being delegated, plus its id and default role.

        `case_id` is read off the Task row rather than taken from the caller, so
        the two can never disagree.
        """
        if task_id:
            holder = await self._tasks().get_task(org_id, task_id, user_id)
            return holder, holder["case_id"], holder.get("task_type") or ""
        holder = await self._cases().assert_case_access(org_id, case_id, user_id)
        return holder, case_id, holder.get("case_type") or ""

    async def prepare_delegation(
        self,
        *,
        org_id: str,
        case_id: str,
        task_id: str | None,
        user_id: str,
        previous_draft_id: str | None = None,
        instruction: str | None = None,
        language: str = DEFAULT_QUERY_LANGUAGE,
    ) -> dict[str, Any]:
        """Compose the request and hand it to the employee to review.

        Deliberately free: no slot is reserved and no credits are checked, so
        opening the review step and closing it again costs nothing. That is what
        lets review be the normal path instead of a confirmation dialog people
        learn to click through.
        """
        # Loading the holder is the permission check, and it is deliberately the
        # same one `delegate` makes: preparing a request must never be harder
        # than sending one, or people learn to skip the review step.
        holder, resolved_case_id, declared = await self._load_delegation_holder(
            org_id, case_id, task_id, user_id
        )

        # A regeneration carries the rejected text forward, so the employee can
        # say what to change instead of describing the whole task again.
        if previous_draft_id:
            previous = await self.load_draft(org_id, previous_draft_id)
            if previous.get("case_id") != resolved_case_id or previous.get(
                "task_id"
            ) != task_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="That draft belongs to different work",
                )
            body = str(previous.get("content") or "").strip()
            if body:
                labels = QUERY_LABELS.get(
                    language, QUERY_LABELS[DEFAULT_QUERY_LANGUAGE]
                )
                tail = instruction or labels["revise"]
                instruction = f"{labels['previous_draft']}:\n{body}\n\n{tail}"

        query = self._build_query(holder, instruction, language)
        return {
            "org_id": org_id,
            "case_id": resolved_case_id,
            "task_id": task_id,
            "query": query,
            "base_hash": self._hash_query(query),
            "assistant": AssistantConfig.resolve_delegation_role(None, declared),
            "roles": AssistantConfig.delegation_roles(),
            "closed": is_closed(holder),
        }

    async def delegate(
        self,
        *,
        org_id: str,
        case_id: str,
        task_id: str | None,
        user_id: str,
        instruction: str | None = None,
        query: str | None = None,
        assistant: str | None = None,
        base_hash: str | None = None,
        language: str = DEFAULT_QUERY_LANGUAGE,
    ) -> Any:
        """Hand a Case or Task to its agent. The answer lands as a pending draft.

        Everything up to the stream runs eagerly, so a refusal is a normal JSON
        error instead of an error event inside a half-open stream.

        `query` is the request the employee reviewed. When it is omitted the
        server composes one itself, which is both the fallback for older clients
        and what keeps the composed text the starting point rather than a
        client-supplied prompt: `query_base` is always recomputed here from the
        record, never accepted from the caller.
        """
        chat = self._chat()
        holder, case_id, declared = await self._load_delegation_holder(
            org_id, case_id, task_id, user_id
        )

        if is_closed(holder):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This work is closed. Reopen it before delegating again.",
            )

        assistant = AssistantConfig.resolve_delegation_role(assistant, declared)
        credit_cost, _ = chat.extract_assistant_config(assistant)

        # Recomputed from the record rather than trusted from the client: this is
        # the half of the pair that has to be the machine's own words for the
        # comparison below to mean anything.
        query_base = self._build_query(holder, instruction, language)
        submitted = (query or "").strip()
        query_final = submitted or query_base
        # A missing or stale hash counts as edited. Over-recording a human touch
        # is the safe direction for evidence; under-recording one is not.
        query_edited = bool(submitted) and (
            base_hash != self._hash_query(query_base)
            or submitted != query_base.strip()
        )

        # Validated before the reservation, so an oversized request is refused
        # while there is still no slot to release. The cap goes on the whole
        # request: the Case fields reach the model too, so checking only the part
        # the client typed would leave the real payload unbounded.
        chat.validate_query_length(query_final)
        query = query_final

        session = await self._history().ensure_shared_session(
            user_id=user_id,
            org_id=org_id,
            project_id=case_id,
            task_id=task_id,
        )
        session_id = session.get("session_id") or session["_id"]
        message_id = self._history().create_message_id()

        # Before credits, deliberately: the loser of a race pays nothing.
        draft_id = await self.reserve_slot(
            org_id=org_id,
            case_id=case_id,
            task_id=task_id,
            session_id=session_id,
            assistant=assistant,
            user_id=user_id,
            query_base=query_base,
            query_final=query_final,
            query_edited=query_edited,
        )

        try:
            await chat.verify_user_credits(
                user_id=user_id,
                assistant_type=assistant,
                required_credits=credit_cost,
            )
        except Exception:
            await self.release_slot(draft_id)
            raise

        await self._logs().record(
            org_id=org_id,
            actor_id=user_id,
            actor_type=ActorType.user,
            event_type=EventType.ai_request_submitted,
            object_type="draft",
            object_id=draft_id,
            object_label=holder.get("title"),
            case_id=case_id,
            task_id=task_id,
            payload={
                "assistant": assistant,
                "has_instruction": bool(instruction),
                "query_edited": query_edited,
            },
        )
        if query_edited:
            # Its own event beside the submission, so "how often does a human
            # actually change the request" is a question the log can answer.
            await self._logs().record(
                org_id=org_id,
                actor_id=user_id,
                actor_type=ActorType.user,
                event_type=EventType.ai_request_edited,
                object_type="draft",
                object_id=draft_id,
                object_label=holder.get("title"),
                case_id=case_id,
                task_id=task_id,
                payload={
                    "assistant": assistant,
                    "chars_before": len(query_base),
                    "chars_after": len(query_final),
                },
            )

        generator = self._stream_and_capture(
            draft_id=draft_id,
            org_id=org_id,
            case_id=case_id,
            task_id=task_id,
            holder=holder,
            session_id=session_id,
            message_id=message_id,
            query=query,
            assistant=assistant,
            user_id=user_id,
        )
        return chat.create_streaming_response(generator)

    async def _stream_and_capture(
        self,
        *,
        draft_id: str,
        org_id: str,
        case_id: str,
        task_id: str | None,
        holder: dict[str, Any],
        session_id: str,
        message_id: str,
        query: str,
        assistant: str,
        user_id: str,
    ) -> AsyncGenerator[Any, None]:
        """Relay the stream, then persist the draft — or free the slot trying."""
        from time import perf_counter

        chat = self._chat()
        completed = False
        chunks: list[str] = []
        # The client needs this before the first token, so it can render the
        # pending shell immediately.
        yield {"type": "delegation", "draft_id": draft_id}
        try:
            inner = chat.astream_orchestrated_chat(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                query=query,
                file_ids=None,
                assistant=assistant,
                started_at=perf_counter(),
                project_id=case_id,
                timeout=settings.AI_DELEGATION_TIMEOUT_SECONDS,
                # Deep analysis is a different inference workflow, not a different
                # prompt: the same split `handle_agentic_rag_stream` makes for
                # chat. Routed on the raw role name because `deepresearch` aliases
                # to `main` the moment it is canonicalised.
                stream_endpoint=(
                    "/api/v1/chat/agent/stream"
                    if AssistantConfig.is_deep_research_assistant(assistant)
                    else "/api/v1/chat/ask/stream"
                ),
            )
            async for item in bounded(inner, settings.AI_DELEGATION_TIMEOUT_SECONDS):
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict) and item.get("type") == "chunk":
                    chunks.append(str(item.get("chunk") or ""))
                yield item

            await detached(
                self.finalize_draft(draft_id, "".join(chunks).strip(), message_id)
            )
            completed = True
            await self._logs().record(
                org_id=org_id,
                actor_id=user_id,
                actor_type=ActorType.agent,
                on_behalf_of=user_id,
                event_type=EventType.ai_draft_generated,
                object_type="draft",
                object_id=draft_id,
                object_label=holder.get("title"),
                case_id=case_id,
                task_id=task_id,
                payload={"draft_id": draft_id, "chain_id": draft_id, "version": 1},
            )
            yield {"type": "end", "draft_id": draft_id, "status": "pending"}
        except Exception as exc:
            await self._logs().record(
                org_id=org_id,
                actor_id=user_id,
                actor_type=ActorType.system,
                event_type=EventType.ai_generation_failed,
                object_type="draft",
                object_id=draft_id,
                object_label=holder.get("title"),
                case_id=case_id,
                task_id=task_id,
                payload={
                    "assistant": assistant,
                    "reason": str(exc),
                    "draft_id": draft_id,
                },
            )
            raise
        finally:
            # `finally`, not `except Exception`: a disconnect raises
            # CancelledError, a BaseException that an Exception handler never
            # sees — and a disconnect is the most common way this ends early.
            if not completed:
                await detached(self.release_slot(draft_id))

    async def assert_no_active_draft(
        self, org_id: str, case_id: str, task_id: str | None = None
    ) -> None:
        """Nothing closes over unconfirmed AI work.

        Called without `task_id` (the Case form) the query has no task predicate,
        so a draft on any child Task blocks the parent too. That is why `case_id`
        is stored on Task rows.
        """
        query: dict[str, Any] = {
            "org_id": org_id,
            "case_id": case_id,
            "slot_active": True,
        }
        if task_id is not None:
            query["task_id"] = task_id

        held = await self._collection().find_one(query)
        if not held:
            return
        where = f"Task {held['task_id']}" if held.get("task_id") else "this Case"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A draft on {where} is awaiting human confirmation",
        )

    # --- decide -------------------------------------------------------------

    async def load_draft(self, org_id: str, draft_id: str) -> dict[str, Any]:
        """404 on a foreign org — never 403, which would confirm the row exists."""
        row = await self._collection().find_one({"_id": draft_id, "org_id": org_id})
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found"
            )
        return row

    async def assert_draft_access(
        self, org_id: str, draft_id: str, user_id: str
    ) -> dict[str, Any]:
        """The draft, if the caller may reach the Case or Task holding it.

        Public so the router does not have to reach into ``_load_holder`` — the
        access rule lives with the row, not with whoever reads it.
        """
        row = await self.load_draft(org_id, draft_id)
        await self._load_holder(org_id, row, user_id)
        return row

    async def render_docx(
        self, org_id: str, draft_id: str, user_id: str
    ) -> tuple[bytes, str]:
        """An approved draft as a Word document, plus the filename to serve it as.

        Only approved rows export. A pending draft has no force yet and a rejected
        one never will, so putting either on letterhead would hand someone a
        document the gate has not passed — the export is the moment the text
        leaves the system, and that is the moment the status has to hold.
        """
        row = await self.load_draft(org_id, draft_id)
        holder = await self._load_holder(org_id, row, user_id)

        if row.get("status") != DraftStatus.approved.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only an approved draft can be exported",
            )
        markdown = draft_markdown(row, holder.get("title"))
        if not markdown:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="This draft has no content to export",
            )
        # pandoc is a subprocess: off the event loop, or it stalls every other
        # request on this worker for the length of the conversion.
        content = await asyncio.to_thread(build_markdown_docx, markdown)
        return content, DRAFT_DOCX_FILENAME

    async def get_chain(
        self, org_id: str, draft_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        """Every version of one draft, oldest first.

        One query on `chain_id` rather than walking `parent_draft_id` link by
        link. Version 1 is always the machine's untouched text.
        """
        row = await self.assert_draft_access(org_id, draft_id, user_id)
        cursor = self._collection().find({"chain_id": row["chain_id"]})
        return await cursor.sort("version", 1).to_list(length=200)

    async def list_for_case(
        self, org_id: str, case_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        await self._cases().assert_case_access(org_id, case_id, user_id)
        # Draft bodies are not listing material, and this one is legal text.
        cursor = self._collection().find(
            {"org_id": org_id, "case_id": case_id}, {"content": 0}
        )
        return await cursor.sort("created_at", -1).to_list(length=200)

    async def list_for_task(
        self, org_id: str, task_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        await self._tasks().get_task(org_id, task_id, user_id)
        cursor = self._collection().find(
            {"org_id": org_id, "task_id": task_id}, {"content": 0}
        )
        return await cursor.sort("created_at", -1).to_list(length=200)

    async def _load_holder(
        self, org_id: str, row: dict[str, Any], user_id: str
    ) -> dict[str, Any]:
        """The Task when the draft is on a Task, else the Case."""
        if row.get("task_id"):
            return await self._tasks().get_task(org_id, row["task_id"], user_id)
        return await self._cases().assert_case_access(org_id, row["case_id"], user_id)

    async def _assert_can_decide(
        self, org_id: str, holder: dict[str, Any], user_id: str
    ) -> None:
        """Assignee, else the creator when unassigned, else an admin.

        The owner fallback applies only when nobody is assigned: an assigned item
        is that person's to confirm. Admin is the escape hatch for when they leave.
        """
        assignee = holder.get("assignee_id")
        if assignee:
            if assignee == user_id:
                return
        # Cases store the creator as owner_id; Tasks store it as created_by.
        elif user_id in (holder.get("owner_id"), holder.get("created_by")):
            return
        await self._orgs().assert_org_admin(org_id, user_id)

    def _assert_pending(self, row: dict[str, Any]) -> None:
        if row.get("status") == DraftStatus.generating.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This draft is still being generated",
            )
        if row.get("status") != DraftStatus.pending.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This draft has already been decided",
            )

    async def _decide(
        self, org_id: str, draft_id: str, user_id: str, new_status: DraftStatus
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Returns the updated row and the Case/Task that holds it."""
        row = await self.load_draft(org_id, draft_id)
        holder = await self._load_holder(org_id, row, user_id)
        await self._assert_can_decide(org_id, holder, user_id)
        # Only approval moves the board, so only approval is blocked by a closed
        # holder — see `approve`. Checked before the write for the same reason.
        if new_status is DraftStatus.approved and is_closed(holder):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This work is closed. Reopen it before approving a draft.",
            )
        self._assert_pending(row)

        now = datetime.now(timezone.utc)
        res = await self._collection().update_one(
            {
                "_id": draft_id,
                "status": DraftStatus.pending.value,
                "slot_active": True,
            },
            {
                "$set": {
                    "status": new_status.value,
                    "decided_by": user_id,
                    "decided_at": now,
                },
                "$unset": {"slot_active": ""},
            },
        )
        if res.matched_count == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This draft has already been decided",
            )
        row.update(
            {"status": new_status.value, "decided_by": user_id, "decided_at": now}
        )
        return row, holder

    async def approve(self, org_id: str, draft_id: str, user_id: str) -> dict[str, Any]:
        """Confirm a draft. The only transition that moves the board.

        `set_state` refuses a closed holder, but refusing there would leave the
        draft already marked approved with the board unmoved — no transactions,
        nothing to roll back. So the holder is checked before `_decide` writes.
        Rejection has no such guard: it moves no board, and it is the only way to
        clear a slot that a race left on a since-closed Case.
        """
        row, holder = await self._decide(
            org_id, draft_id, user_id, DraftStatus.approved
        )
        state_move = await self._move_to_in_review(org_id, row, user_id)
        await self._log_decision(
            row,
            holder,
            user_id,
            EventType.ai_draft_approved,
            {"state_move": state_move},
        )
        return row

    async def reject(self, org_id: str, draft_id: str, user_id: str) -> dict[str, Any]:
        """Refuse a draft. Terminal — a later delegation starts a new chain."""
        row, holder = await self._decide(
            org_id, draft_id, user_id, DraftStatus.rejected
        )
        await self._log_decision(row, holder, user_id, EventType.ai_draft_rejected, {})
        return row

    async def edit(
        self, org_id: str, draft_id: str, user_id: str, content: str
    ) -> dict[str, Any]:
        """A human rewrite is a new row, never an overwrite.

        Version 1 keeps the machine's untouched text forever — that permanence is
        what the law is actually asking for.
        """
        row = await self.load_draft(org_id, draft_id)
        holder = await self._load_holder(org_id, row, user_id)
        await self._assert_can_decide(org_id, holder, user_id)
        self._assert_pending(row)

        now = datetime.now(timezone.utc)
        # Supersede first. Insert-first would put two rows in the slot and lose to
        # the unique index.
        res = await self._collection().update_one(
            {
                "_id": draft_id,
                "status": DraftStatus.pending.value,
                "slot_active": True,
            },
            {
                "$set": {"status": DraftStatus.superseded.value},
                "$unset": {"slot_active": ""},
            },
        )
        if res.matched_count == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This draft has already been decided",
            )

        # The slot is briefly empty here, so a closure can slip in. Re-reading
        # narrows that window; with no transactions it cannot be closed entirely.
        # Through _load_holder, so a Task draft re-reads its Task: Tasks carry
        # their own closure block, and checking the parent Case would miss it.
        fresh = await self._load_holder(org_id, row, user_id)
        if is_closed(fresh):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This work was closed while you were editing",
            )

        new_id = generate_short_id(prefix=self.prefix, type="uuid7")
        new_row = clean_for_mongodb(
            {
                "_id": new_id,
                "org_id": org_id,
                "case_id": row["case_id"],
                "task_id": row.get("task_id"),
                "session_id": row["session_id"],
                "message_id": None,
                "chain_id": row["chain_id"],
                "parent_draft_id": draft_id,
                "version": row["version"] + 1,
                "source": DraftSource.human.value,
                "content": content,
                # Carried forward, not recomposed: every version in a chain
                # answers the same request, and a reader of version 3 should not
                # have to walk back to version 1 to find out what it was.
                "query_base": row.get("query_base", ""),
                "query_final": row.get("query_final", ""),
                "query_edited": row.get("query_edited", False),
                "status": DraftStatus.pending.value,
                "slot_active": True,
                "assistant": row["assistant"],
                "created_by": user_id,
                "decided_by": None,
                "decided_at": None,
                "edited": True,
                "created_at": now,
            }
        )
        try:
            await self._collection().insert_one(new_row)
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Someone delegated while you were editing",
            ) from exc

        await self._logs().record(
            org_id=org_id,
            event_type=EventType.ai_draft_edited,
            actor_id=user_id,
            actor_type=ActorType.user,
            object_type="draft",
            object_id=new_id,
            object_label=holder.get("title"),
            case_id=row["case_id"],
            task_id=row.get("task_id"),
            payload={
                "draft_id": new_id,
                "chain_id": row["chain_id"],
                "from_version": row["version"],
                "to_version": row["version"] + 1,
                "chars_before": len(row.get("content") or ""),
                "chars_after": len(content),
            },
        )
        return new_row

    async def _move_to_in_review(
        self, org_id: str, row: dict[str, Any], user_id: str
    ) -> str:
        """Board convenience, not the legally meaningful act.

        Never raises: the decision is already durable, and telling the member their
        approval failed when it succeeded would be worse than a stale column.
        """
        applies_to = StateAppliesTo.task if row.get("task_id") else StateAppliesTo.case
        try:
            state = await self._states().default_state_for(
                org_id, applies_to, StateCategory.in_review
            )
            if not state:
                # A board may legitimately have no in_review column.
                return "skipped_no_in_review_state"
            # set_state re-reads the row and applies the owning service's own
            # update rules. Re-read rather than reusing the doc from the access
            # check, which was loaded before the decision write. No permission
            # check inside it — the decision above already authorized this move.
            if row.get("task_id"):
                await self._tasks().set_state(
                    org_id, row["task_id"], str(state["_id"]), user_id
                )
            else:
                await self._cases().set_state(
                    org_id, row["case_id"], str(state["_id"]), user_id
                )
            return "moved"
        except Exception as exc:  # noqa: BLE001 - the decision stands regardless
            logger.warning(
                f"[DraftService] Approved {row['_id']} but the board move failed: {exc}"
            )
            return "failed"

    async def _log_decision(
        self,
        row: dict[str, Any],
        holder: dict[str, Any],
        user_id: str,
        event: EventType,
        extra: dict[str, Any],
    ) -> None:
        await self._logs().record(
            org_id=row["org_id"],
            event_type=event,
            actor_id=user_id,
            actor_type=ActorType.user,
            object_type="draft",
            object_id=row["_id"],
            object_label=holder.get("title"),
            case_id=row["case_id"],
            task_id=row.get("task_id"),
            # No draft text: both versions are already rows, and duplicating them
            # into an append-only log doubles the storage and the leak surface.
            payload={
                "draft_id": row["_id"],
                "chain_id": row["chain_id"],
                "version": row["version"],
                **extra,
            },
        )


_draft_service: DraftService | None = None
