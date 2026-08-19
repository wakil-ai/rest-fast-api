"""Shared pytest fixtures.

Existing tests each build their own bare ``FastAPI()`` + router (see
test_archive_guard.py, test_upload_entitlement_integration.py) rather than
importing ``main.create_app()``, so the coded-error handlers registered in
``core.error_handlers`` are not applied unless a test opts in explicitly.
``app_with_error_handlers`` is that opt-in for tests that need to assert on
the error envelope shape.
"""

import uuid

import pytest
from fastapi import APIRouter, FastAPI

from core.config import settings
from core.error_handlers import register_exception_handlers

SUPER_ADMIN_TEST_KEY = "test-super-admin-key"


def make_app_with_error_handlers() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    return app


def make_admin_app(router: APIRouter) -> FastAPI:
    """Bare app with the coded-error envelope, for admin-guarded routers."""
    app = make_app_with_error_handlers()
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
