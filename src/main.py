import os
import sys

# Make this package's directory importable so internal modules can be referenced
# without the package prefix (e.g. `from core.config import settings` instead of
# `from src.core.config import settings`).
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from core.warnings_config import configure_startup_warnings

configure_startup_warnings()

# FastAPI imports
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from starlette.middleware.sessions import SessionMiddleware

# Internal imports
from api.v2 import (
    admin,
    auth,
    chat,
    fingerprint,
    memory,
    organizations,
    otp,
    payment,
    promo_codes,
    referral,
    speech_to_text,
)
from api.v2.history.router import router as chat_history
from api.v2.history.share import router as share_router
from api.v3 import chat as v3_chat
from api.internal import router as internal_router
from core.config import settings
from core.logger import logger
from security import (
    get_current_username,
    verify_api_key_or_dt_key,
    verify_not_archived,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    logger.info("Started WakilAI API application")

    # No OpenTelemetry exporters are wired up in this service.
    os.environ["OTEL_SDK_DISABLED"] = "true"

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.VERSION,
        debug=settings.DEBUG,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # Add CORS middleware first
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(settings.ALLOWED_ORIGINS if not settings.DEBUG else ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Session middleware - MUST be added before OAuth middleware
    # Using permissive settings for OAuth state management across redirects
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.AUTH_SECRET_KEY,
        session_cookie="wakil_session",
        https_only=False,  # Allow HTTP in dev/behind proxy
        same_site="lax",  # Required for third-party redirects (Google)
        max_age=3600,  # 1 hour session timeout
    )

    # Mount routers with API key authentication.
    # `verify_not_archived` blocks any request made on behalf of a soft-deleted
    # (archived) account across the user-facing surface.
    app.include_router(
        chat.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key_or_dt_key), Depends(verify_not_archived)],
    )
    app.include_router(
        v3_chat.router,
        prefix="/api/v3",
        dependencies=[Depends(verify_api_key_or_dt_key), Depends(verify_not_archived)],
    )
    app.include_router(
        chat_history,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key_or_dt_key), Depends(verify_not_archived)],
    )
    app.include_router(
        speech_to_text.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key_or_dt_key), Depends(verify_not_archived)],
    )
    app.include_router(
        memory.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key_or_dt_key), Depends(verify_not_archived)],
    )
    app.include_router(
        fingerprint.router,
        prefix=settings.API_PREFIX,
    )
    app.include_router(
        organizations.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key_or_dt_key), Depends(verify_not_archived)],
    )
    app.include_router(admin.router, prefix=settings.API_PREFIX)
    app.include_router(promo_codes.router, prefix=settings.API_PREFIX)
    # Auth routers without API prefix
    app.include_router(auth.router, prefix=settings.API_PREFIX)
    app.include_router(otp.router, prefix=settings.API_PREFIX)
    app.include_router(payment.router, prefix=settings.API_PREFIX)
    app.include_router(referral.router, prefix=settings.API_PREFIX)
    app.include_router(share_router, prefix=settings.API_PREFIX, tags=["Public"])
    app.include_router(internal_router)

    # Health Check Route (no authentication required)
    @app.get("/", tags=["Health"], include_in_schema=False)
    @app.get("/health", tags=["Health"])
    async def health_check():
        return {"status": "ok", "message": "WakilAI API is running 🚀"}

    # Documentation endpoints (Basic Auth protected)
    @app.get("/docs")
    async def docs(username: str = Depends(get_current_username)):
        return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")

    @app.get("/redoc")
    async def redoc(username: str = Depends(get_current_username)):
        return get_redoc_html(openapi_url="/openapi.json", title="redocs")

    @app.get("/swagger-ui.html")
    async def swagger_ui(username: str = Depends(get_current_username)):
        return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")

    @app.get("/openapi.json")
    async def openapi(username: str = Depends(get_current_username)):
        return get_openapi(title="FastAPI", version="0.1.0", routes=app.routes)

    return app


app = create_app()
