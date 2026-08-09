"""Admin-only organization edits: PATCH of name/settings and avatar upload."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.v2 import organizations as organizations_api
from core.config import settings
from security import dependencies as security_dependencies
from services import organization_service as organization_service_module
from services.organization_service import MAX_AVATAR_BYTES, OrganizationService


@pytest.fixture
def jwt_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "test-jwt-secret-at-least-32-bytes")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "JWT_ISSUER", "test-issuer")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "test-audience")


@pytest.fixture(autouse=True)
def auth_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        security_dependencies.redis_service, "cache_get", MagicMock(return_value=None)
    )
    monkeypatch.setattr(
        security_dependencies.redis_service, "cache_set", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(
            return_value={"exists": True, "is_blocked": False, "archived": False}
        ),
    )


def frontend_token(user_id: str) -> str:
    secret = settings.JWT_SECRET_KEY
    assert secret is not None, "jwt_settings fixture must run before minting a token"

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid.uuid4().hex,
            "typ": "access_token",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        },
        secret,
        algorithm=settings.JWT_ALGORITHM,
    )


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(organizations_api.router, prefix="/api/v2")
    return TestClient(app)


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {frontend_token(user_id)}"}


def org_doc(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc: dict[str, Any] = {
        "_id": "org-1",
        "name": "Acme",
        "avatar_path": None,
        "created_by": "user-a",
        "status": "active",
        "seat_limit": 10,
        "settings": {},
        "archived": False,
        "created_at": now,
        "updated_at": now,
        "role": "admin",
        "member_count": 1,
    }
    doc.update(overrides)
    return doc


# --- PATCH route wiring -----------------------------------------------------


def test_patch_rejects_requests_without_a_bearer_token(client: TestClient) -> None:
    response = client.patch("/api/v2/organizations/org-1", json={"name": "New"})
    assert response.status_code == 401


def test_patch_uses_the_token_subject_and_omits_untouched_fields(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    updater = AsyncMock(return_value=org_doc(name="New"))
    monkeypatch.setattr(organizations_api.org_service, "update_organization", updater)

    response = client.patch(
        "/api/v2/organizations/org-1", json={"name": "New"}, headers=auth("user-a")
    )

    assert response.status_code == 200
    # settings is absent, not None: an untouched field must not be overwritten.
    updater.assert_awaited_once_with(
        org_id="org-1", user_id="user-a", updates={"name": "New"}
    )


def test_patch_forwards_an_explicit_null_so_a_field_can_be_reset(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    updater = AsyncMock(return_value=org_doc())
    monkeypatch.setattr(organizations_api.org_service, "update_organization", updater)

    response = client.patch(
        "/api/v2/organizations/org-1", json={"settings": None}, headers=auth("user-a")
    )

    assert response.status_code == 200
    updater.assert_awaited_once_with(
        org_id="org-1", user_id="user-a", updates={"settings": None}
    )


def test_patch_ignores_a_user_id_in_the_body(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    updater = AsyncMock(return_value=org_doc())
    monkeypatch.setattr(organizations_api.org_service, "update_organization", updater)

    response = client.patch(
        "/api/v2/organizations/org-1",
        json={"name": "New", "user_id": "somebody-else"},
        headers=auth("user-a"),
    )

    assert response.status_code == 200
    updater.assert_awaited_once_with(
        org_id="org-1", user_id="user-a", updates={"name": "New"}
    )


def test_avatar_path_is_built_from_the_configured_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derived on read, so a row stored under an older prefix cannot serve a stale
    path. The stored string is only a presence flag."""
    monkeypatch.setattr(settings, "API_PREFIX", "/api/v9")

    response = organizations_api._org_to_response(
        org_doc(avatar_path="/api/v2/organizations/org-1/avatar")
    )

    assert response.avatar_path == "/api/v9/organizations/org-1/avatar"


def test_avatar_path_stays_null_when_there_is_no_picture() -> None:
    assert organizations_api._org_to_response(org_doc()).avatar_path is None


# --- avatar route wiring ----------------------------------------------------


def test_avatar_upload_rejects_requests_without_a_bearer_token(
    client: TestClient,
) -> None:
    response = client.put(
        "/api/v2/organizations/org-1/avatar",
        files={"file": ("logo.png", b"bytes", "image/png")},
    )
    assert response.status_code == 401


def test_avatar_upload_forwards_the_bytes_and_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    setter = AsyncMock(
        return_value=org_doc(avatar_path="/api/v2/organizations/org-1/avatar")
    )
    monkeypatch.setattr(
        organizations_api.org_service, "set_organization_avatar", setter
    )

    response = client.put(
        "/api/v2/organizations/org-1/avatar",
        files={"file": ("logo.png", b"\x89PNG-bytes", "image/png")},
        headers=auth("user-a"),
    )

    assert response.status_code == 200
    assert response.json()["avatar_path"] == "/api/v2/organizations/org-1/avatar"
    setter.assert_awaited_once_with(
        org_id="org-1",
        user_id="user-a",
        data=b"\x89PNG-bytes",
        content_type="image/png",
    )


def test_avatar_delete_rejects_requests_without_a_bearer_token(
    client: TestClient,
) -> None:
    assert client.delete("/api/v2/organizations/org-1/avatar").status_code == 401


def test_avatar_delete_uses_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    clearer = AsyncMock(return_value=org_doc())
    monkeypatch.setattr(
        organizations_api.org_service, "clear_organization_avatar", clearer
    )

    response = client.delete(
        "/api/v2/organizations/org-1/avatar", headers=auth("user-a")
    )

    assert response.status_code == 200
    assert response.json()["avatar_path"] is None
    clearer.assert_awaited_once_with(org_id="org-1", user_id="user-a")


def test_avatar_upload_rejects_an_oversized_body_before_reading_it(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    setter = AsyncMock(return_value=org_doc())
    monkeypatch.setattr(
        organizations_api.org_service, "set_organization_avatar", setter
    )

    response = client.put(
        "/api/v2/organizations/org-1/avatar",
        files={"file": ("big.png", b"x" * (MAX_AVATAR_BYTES + 1), "image/png")},
        headers=auth("user-a"),
    )

    assert response.status_code == 413
    # Rejected in the route, so the body never reached the admin check or memory.
    setter.assert_not_awaited()


# --- service ----------------------------------------------------------------


def build_service() -> tuple[OrganizationService, AsyncMock]:
    svc = OrganizationService.__new__(OrganizationService)
    db = AsyncMock()
    svc.db = db  # type: ignore[misc]
    svc.orgs_collection = "organizations"
    svc.members_collection = "organization_members"
    svc.org_prefix = "org-"
    svc.member_prefix = "omem-"
    svc.assert_org_admin = AsyncMock(return_value=org_doc(), unsafe=True)  # type: ignore[method-assign]
    svc.active_member_count = AsyncMock(return_value=3)  # type: ignore[method-assign]
    return svc, db


def sent_update(db: AsyncMock) -> dict[str, Any]:
    """The ``$set`` document handed to Mongo by the last update_documents call."""
    return db.update_documents.await_args.args[2]["$set"]


async def test_update_writes_only_the_supplied_fields() -> None:
    svc, db = build_service()

    result = await svc.update_organization(
        org_id="org-1", user_id="user-a", updates={"name": "Renamed"}
    )

    update = sent_update(db)
    assert update["name"] == "Renamed"
    assert "settings" not in update
    assert update["updated_at"] is not None
    assert result["name"] == "Renamed"
    assert result["role"] == "admin"
    assert result["member_count"] == 3


async def test_update_cannot_write_privileged_fields_or_the_avatar() -> None:
    svc, db = build_service()

    await svc.update_organization(
        org_id="org-1",
        user_id="user-a",
        updates={
            "name": "New",
            # avatar_path is written by the upload path only; a value supplied
            # here would point members' clients at a route we never validated.
            "avatar_path": "https://attacker/pixel.gif",
            "status": "suspended",
            "seat_limit": 9999,
            "archived": True,
            "created_by": "attacker",
        },
    )

    assert set(sent_update(db)) == {"name", "updated_at"}


async def test_update_rejects_a_blank_name() -> None:
    svc, db = build_service()

    with pytest.raises(HTTPException) as exc:
        await svc.update_organization(
            org_id="org-1", user_id="user-a", updates={"name": "   "}
        )

    assert exc.value.status_code == 400
    db.update_documents.assert_not_awaited()


async def test_update_trims_the_name() -> None:
    svc, db = build_service()

    await svc.update_organization(
        org_id="org-1", user_id="user-a", updates={"name": "  Acme Corp  "}
    )

    assert sent_update(db)["name"] == "Acme Corp"


async def test_update_requires_admin_before_writing() -> None:
    svc, db = build_service()
    svc.assert_org_admin = AsyncMock(  # type: ignore[method-assign]
        side_effect=HTTPException(status_code=403, detail="Only an organization admin"),
        unsafe=True,
    )

    with pytest.raises(HTTPException) as exc:
        await svc.update_organization(
            org_id="org-1", user_id="outsider", updates={"name": "Taken Over"}
        )

    assert exc.value.status_code == 403
    db.update_documents.assert_not_awaited()


async def test_update_with_an_empty_body_touches_nothing() -> None:
    svc, db = build_service()

    result = await svc.update_organization(org_id="org-1", user_id="user-a", updates={})

    db.update_documents.assert_not_awaited()
    assert result["name"] == "Acme"
    assert result["member_count"] == 3


# --- avatar service ---------------------------------------------------------


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    # No return_value: upload_file's return is discarded now that the stored
    # value is an API path rather than anything storage hands back.
    stub = MagicMock()
    monkeypatch.setattr(
        organization_service_module, "get_storage_service", lambda: stub
    )
    return stub


async def test_avatar_upload_stores_the_api_path(storage: MagicMock) -> None:
    svc, db = build_service()

    result = await svc.set_organization_avatar(
        org_id="org-1", user_id="user-a", data=b"png-bytes", content_type="image/png"
    )

    storage.upload_file.assert_called_once_with(
        data=b"png-bytes",
        destination_path="organizations/org-1/avatar",
        content_type="image/png",
        return_signed_url=False,
    )
    update = sent_update(db)
    # No ?v= stamp: the value is not a URL a browser caches. It never expires and
    # never changes, so a replacement upload needs nothing written here at all.
    assert update["avatar_path"] == "/api/v2/organizations/org-1/avatar"
    assert "avatar_url" not in update
    assert result["avatar_path"] == update["avatar_path"]


async def test_avatar_upload_stores_the_path_under_the_configured_prefix(
    storage: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "API_PREFIX", "/api/v9")
    svc, db = build_service()

    await svc.set_organization_avatar(
        org_id="org-1", user_id="user-a", data=b"png", content_type="image/png"
    )

    assert sent_update(db)["avatar_path"] == "/api/v9/organizations/org-1/avatar"


async def test_avatar_upload_accepts_a_charset_suffixed_content_type(
    storage: MagicMock,
) -> None:
    svc, _ = build_service()

    await svc.set_organization_avatar(
        org_id="org-1",
        user_id="user-a",
        data=b"jpeg-bytes",
        content_type="image/jpeg; charset=binary",
    )

    assert storage.upload_file.call_args.kwargs["content_type"] == "image/jpeg"


@pytest.mark.parametrize(
    "content_type", ["image/svg+xml", "application/pdf", "text/html", None]
)
async def test_avatar_upload_rejects_non_image_types(
    storage: MagicMock, content_type: str | None
) -> None:
    svc, db = build_service()

    with pytest.raises(HTTPException) as exc:
        await svc.set_organization_avatar(
            org_id="org-1", user_id="user-a", data=b"x", content_type=content_type
        )

    assert exc.value.status_code == 415
    storage.upload_file.assert_not_called()
    db.update_documents.assert_not_awaited()


async def test_avatar_upload_rejects_an_empty_file(storage: MagicMock) -> None:
    svc, _ = build_service()

    with pytest.raises(HTTPException) as exc:
        await svc.set_organization_avatar(
            org_id="org-1", user_id="user-a", data=b"", content_type="image/png"
        )

    assert exc.value.status_code == 400
    storage.upload_file.assert_not_called()


async def test_avatar_upload_rejects_an_oversized_file(storage: MagicMock) -> None:
    svc, _ = build_service()

    with pytest.raises(HTTPException) as exc:
        await svc.set_organization_avatar(
            org_id="org-1",
            user_id="user-a",
            data=b"x" * (MAX_AVATAR_BYTES + 1),
            content_type="image/png",
        )

    assert exc.value.status_code == 413
    storage.upload_file.assert_not_called()


async def test_avatar_delete_removes_the_object_and_the_path(
    storage: MagicMock,
) -> None:
    svc, db = build_service()
    svc.assert_org_admin = AsyncMock(  # type: ignore[method-assign]
        return_value=org_doc(avatar_path="/api/v2/organizations/org-1/avatar"),
        unsafe=True,
    )

    result = await svc.clear_organization_avatar(org_id="org-1", user_id="user-a")

    storage.permanently_delete_file.assert_called_once_with(
        "organizations/org-1/avatar"
    )
    assert sent_update(db)["avatar_path"] is None
    assert result["avatar_path"] is None


async def test_avatar_delete_skips_storage_when_there_is_no_picture(
    storage: MagicMock,
) -> None:
    svc, db = build_service()

    await svc.clear_organization_avatar(org_id="org-1", user_id="user-a")

    storage.permanently_delete_file.assert_not_called()
    assert sent_update(db)["avatar_path"] is None


async def test_avatar_delete_clears_the_path_even_if_storage_fails(
    storage: MagicMock,
) -> None:
    svc, db = build_service()
    svc.assert_org_admin = AsyncMock(  # type: ignore[method-assign]
        return_value=org_doc(avatar_path="/api/v2/organizations/org-1/avatar"),
        unsafe=True,
    )
    storage.permanently_delete_file.return_value = False

    result = await svc.clear_organization_avatar(org_id="org-1", user_id="user-a")

    assert sent_update(db)["avatar_path"] is None
    assert result["avatar_path"] is None


async def test_avatar_delete_requires_admin(storage: MagicMock) -> None:
    svc, db = build_service()
    svc.assert_org_admin = AsyncMock(  # type: ignore[method-assign]
        side_effect=HTTPException(status_code=403, detail="Only an organization admin"),
        unsafe=True,
    )

    with pytest.raises(HTTPException) as exc:
        await svc.clear_organization_avatar(org_id="org-1", user_id="member")

    assert exc.value.status_code == 403
    storage.permanently_delete_file.assert_not_called()
    db.update_documents.assert_not_awaited()


async def test_creating_an_organization_leaves_the_avatar_unset() -> None:
    """An admin uploads a picture later, or never — create takes no image."""
    svc, db = build_service()

    result = await svc.create_organization(user_id="user-a", name="Acme")

    assert result["avatar_path"] is None
    inserted = db.insert_documents.await_args_list[0].args[1][0]
    assert inserted["avatar_path"] is None
    assert "avatar_url" not in inserted


async def test_avatar_upload_requires_admin_before_touching_storage(
    storage: MagicMock,
) -> None:
    svc, db = build_service()
    svc.assert_org_admin = AsyncMock(  # type: ignore[method-assign]
        side_effect=HTTPException(status_code=403, detail="Only an organization admin"),
        unsafe=True,
    )

    with pytest.raises(HTTPException) as exc:
        await svc.set_organization_avatar(
            org_id="org-1", user_id="member", data=b"png", content_type="image/png"
        )

    assert exc.value.status_code == 403
    storage.upload_file.assert_not_called()
    db.update_documents.assert_not_awaited()


async def test_avatar_upload_targets_the_default_private_bucket(
    storage: MagicMock,
) -> None:
    """No bucket_name argument at all — the default bucket is the private one."""
    svc, _ = build_service()

    await svc.set_organization_avatar(
        org_id="org-1", user_id="user-a", data=b"png-bytes", content_type="image/png"
    )

    assert "bucket_name" not in storage.upload_file.call_args.kwargs


async def test_avatar_delete_targets_the_default_private_bucket(
    storage: MagicMock,
) -> None:
    svc, _ = build_service()
    svc.assert_org_admin = AsyncMock(  # type: ignore[method-assign]
        return_value=org_doc(avatar_path="/api/v2/organizations/org-1/avatar"),
        unsafe=True,
    )

    await svc.clear_organization_avatar(org_id="org-1", user_id="user-a")

    assert "bucket_name" not in storage.permanently_delete_file.call_args.kwargs


def test_blob_key_helper_is_named_for_what_it_returns() -> None:
    """``avatar_path`` on the document is an API path; this is the GCS object key."""
    from services.organization_service import avatar_blob_key

    assert avatar_blob_key("org-1") == "organizations/org-1/avatar"


# --- avatar read endpoint ---------------------------------------------------


def build_member_service(**org_overrides: Any) -> OrganizationService:
    """A service whose membership check passes, for the read-only avatar route."""
    svc, _ = build_service()
    svc.assert_org_member = AsyncMock(  # type: ignore[method-assign]
        return_value=org_doc(**org_overrides), unsafe=True
    )
    return svc


async def test_avatar_url_endpoint_returns_a_signed_url(storage: MagicMock) -> None:
    storage.get_signed_url.return_value = "https://storage/signed?sig=x"
    svc = build_member_service(avatar_path="/api/v2/organizations/org-1/avatar")

    result = await svc.get_organization_avatar_url(org_id="org-1", user_id="member")

    assert result["url"] == "https://storage/signed?sig=x"
    assert result["expires_at"] > datetime.now(timezone.utc)
    # The blob key, not the API path stored on the document.
    assert storage.get_signed_url.call_args.args[0] == "organizations/org-1/avatar"


async def test_avatar_url_404s_when_the_org_has_no_picture(
    storage: MagicMock,
) -> None:
    svc = build_member_service(avatar_path=None)

    with pytest.raises(HTTPException) as exc:
        await svc.get_organization_avatar_url(org_id="org-1", user_id="member")

    assert exc.value.status_code == 404
    storage.get_signed_url.assert_not_called()


async def test_avatar_url_signing_failure_is_503_not_500(storage: MagicMock) -> None:
    """A GCS hiccup must not look like a broken endpoint."""
    storage.get_signed_url.side_effect = RuntimeError("no credentials")
    svc = build_member_service(avatar_path="/api/v2/organizations/org-1/avatar")

    with pytest.raises(HTTPException) as exc:
        await svc.get_organization_avatar_url(org_id="org-1", user_id="member")

    assert exc.value.status_code == 503


async def test_avatar_url_refuses_a_non_member(storage: MagicMock) -> None:
    svc, _ = build_service()
    svc.assert_org_member = AsyncMock(  # type: ignore[method-assign]
        side_effect=HTTPException(status_code=403, detail="not a member"), unsafe=True
    )

    with pytest.raises(HTTPException) as exc:
        await svc.get_organization_avatar_url(org_id="org-1", user_id="stranger")

    assert exc.value.status_code == 403
    storage.get_signed_url.assert_not_called()


def test_avatar_url_route_is_not_cached(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """A cached body would hand a later reader a signature that has since died."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    monkeypatch.setattr(
        organizations_api.org_service,
        "get_organization_avatar_url",
        AsyncMock(return_value={"url": "https://storage/signed", "expires_at": expires}),
    )

    response = client.get(
        "/api/v2/organizations/org-1/avatar", headers=auth("user-a")
    )

    assert response.status_code == 200
    assert response.json()["url"] == "https://storage/signed"
    assert response.headers["cache-control"] == "private, no-store"


def test_avatar_url_route_rejects_requests_without_a_bearer_token(
    client: TestClient,
) -> None:
    assert client.get("/api/v2/organizations/org-1/avatar").status_code == 401
