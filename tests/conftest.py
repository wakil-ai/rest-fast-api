"""Shared pytest fixtures.

Existing tests each build their own bare ``FastAPI()`` + router (see
test_archive_guard.py, test_upload_entitlement_integration.py) rather than
importing ``main.create_app()``, so the coded-error handlers registered in
``core.error_handlers`` are not applied unless a test opts in explicitly.
``app_with_error_handlers`` is that opt-in for tests that need to assert on
the error envelope shape.
"""

from fastapi import FastAPI

from core.error_handlers import register_exception_handlers


def make_app_with_error_handlers() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    return app
