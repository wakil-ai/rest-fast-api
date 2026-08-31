"""Shared pytest fixtures for the admin subscription surface.

No coded-error-envelope layer exists on this branch (that is a `dev`-only
refactor) — admin errors are plain ``HTTPException`` with a dict-shaped
``detail``, rendered by FastAPI's own default handler. So unlike a
conftest built against that envelope, ``make_admin_app`` needs no
exception-handler registration at all.
"""

import uuid

import pytest
from fastapi import APIRouter, FastAPI

from core.config import settings

SUPER_ADMIN_TEST_KEY = "test-super-admin-key"


def make_admin_app(router: APIRouter) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix=settings.API_PREFIX)
    return app


@pytest.fixture
def super_admin_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin the super-admin key.

    ``settings.SUPER_ADMIN_API_KEY`` has a usable default, so without this a test
    would pass against whatever a developer's ``.env`` happens to hold.
    """
    monkeypatch.setattr(settings, "SUPER_ADMIN_API_KEY", SUPER_ADMIN_TEST_KEY)
    return SUPER_ADMIN_TEST_KEY


@pytest.fixture
def super_admin_headers(super_admin_key: str) -> dict[str, str]:
    return {
        settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key,
        settings.ADMIN_OPERATOR_HEADER_NAME.lower(): "test-operator",
        settings.ADMIN_REQUEST_ID_HEADER_NAME.lower(): str(uuid.uuid4()),
    }
