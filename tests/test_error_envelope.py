"""Asserts the coded error envelope core.error_handlers produces.

Covers every shape core.error_codes documents: a coded ChatException, a
legacy dict-detail HTTPException (the pre-existing 402/409 contract), an
uncoded bare HTTPException, a 422 validation error, and an unhandled
exception. `detail` must always be a string — that is the one guarantee
both mobile clients depend on (see docs/reference-error-codes.md).
"""

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests.conftest import make_app_with_error_handlers
from core.error_codes import ErrorCode
from core.exceptions import InsufficientCreditsException, SessionNotFoundError


def _client() -> TestClient:
    app = make_app_with_error_handlers()

    @app.get("/coded")
    async def _coded():
        raise InsufficientCreditsException(credits_remaining=0, limit=30, required_credits=1)

    @app.get("/not-found")
    async def _not_found():
        raise SessionNotFoundError(session_id="sess-123")

    @app.get("/legacy-dict-detail")
    async def _legacy():
        raise HTTPException(
            status_code=402,
            detail={
                "code": "FILE_UPLOAD_REQUIRES_PAID_PLAN",
                "message": "File upload is available for paid plans only.",
                "upgrade_required": True,
            },
        )

    @app.get("/uncoded")
    async def _uncoded():
        raise HTTPException(status_code=404, detail="Widget not found")

    class Body(BaseModel):
        title: str

    @app.post("/validate")
    async def _validate(body: Body):
        return {"ok": True}

    @app.get("/unhandled")
    async def _unhandled():
        raise RuntimeError("db connection reset by peer at 10.0.4.12:27017")

    return TestClient(app, raise_server_exceptions=False)


def test_coded_exception_carries_its_error_code():
    resp = _client().get("/coded")
    assert resp.status_code == 429
    body = resp.json()
    assert isinstance(body["detail"], str)
    assert "0/30" in body["detail"]
    assert body["error"]["code"] == ErrorCode.CREDITS_EXHAUSTED.value
    assert body["error"]["params"] == {"remaining": 0, "limit": 30, "required": 1}


def test_history_exception_carries_its_error_code():
    resp = _client().get("/not-found")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == ErrorCode.SESSION_NOT_FOUND.value
    assert body["error"]["params"] == {"session_id": "sess-123"}


def test_legacy_dict_detail_is_unwrapped_to_string_detail():
    """The pre-existing 402 upload-denial contract: detail used to BE the
    dict. It must come out the other side as a string, with the dict's
    fields moved onto the `error` sibling — this is what keeps the iOS
    client's `detail: String` decode working."""
    resp = _client().get("/legacy-dict-detail")
    assert resp.status_code == 402
    body = resp.json()
    assert isinstance(body["detail"], str)
    assert body["detail"] == "File upload is available for paid plans only."
    assert body["error"]["code"] == "FILE_UPLOAD_REQUIRES_PAID_PLAN"
    assert body["error"]["params"] == {"upgrade_required": True}


def test_uncoded_http_exception_gets_a_status_derived_fallback_code():
    resp = _client().get("/uncoded")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "Widget not found"
    assert body["error"]["code"] == ErrorCode.NOT_FOUND.value


def test_validation_error_becomes_structured_fields_not_a_raw_array():
    resp = _client().post("/validate", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], str)
    assert body["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
    fields = body["error"]["params"]["fields"]
    assert fields and fields[0]["field"] == "title"


def test_unhandled_exception_never_leaks_detail_by_default():
    resp = _client().get("/unhandled")
    assert resp.status_code == 500
    body = resp.json()
    assert "10.0.4.12" not in body["detail"]
    assert body["error"]["code"] == ErrorCode.INTERNAL_ERROR.value


def test_unhandled_exception_can_expose_detail_when_opted_in(monkeypatch):
    from core.config import settings

    # monkeypatch reverts this automatically at test teardown, so the flag
    # can't leak its True value into any other test in the session.
    monkeypatch.setattr(settings, "EXPOSE_ERROR_DETAIL", True)
    resp = _client().get("/unhandled")
    assert resp.status_code == 500
    assert "10.0.4.12" in resp.json()["detail"]
