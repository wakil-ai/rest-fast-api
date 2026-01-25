import os
import secrets

# FastAPI imports
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials
from starlette.middleware.sessions import SessionMiddleware

# Internal imports
from app.api import (
    admin,
    chat,
    chat_history,
    health,
    memory,
    payme,
    retrieval,
    speech_to_text,
    auth,
)
from app.core.config import settings
from app.core.logger import logger

security = HTTPBasic()

# API Key authentication
api_key_header = APIKeyHeader(
    name=settings.API_KEY_NAME.lower(),  # make sure it matches lowercase
    auto_error=False,
    description="HBAI API Key",
)


def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.DOCS_USER)
    correct_password = secrets.compare_digest(
        credentials.password, settings.DOCS_PASSWORD
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def verify_api_key(api_key: str = Security(api_key_header)):
    """
    Verify API key authentication
    Checks both API key name and API key value from settings
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Check if the provided API key matches the one in settings
    if not secrets.compare_digest(api_key, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    logger.info("Started WakilAI API application")

    # Disable all OpenTelemetry (including CrewAI)
    os.environ["OTEL_SDK_DISABLED"] = "true"
    if settings.TRACING:
        os.environ["CREWAI_TRACING_ENABLED"] = "true"

    yield
    # Shutdown (if needed)


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
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Session middleware - using more permissive settings for debugging protocol issues
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.AUTH_SECRET_KEY,
        session_cookie="wakil_session",
        https_only=False,  # Allow HTTP/HTTPS for session to avoid protocol mismatches behind proxy
        same_site="lax",
    )

    # Middleware to handle HTTPS redirect behind proxy
    @app.middleware("http")
    async def proxy_protocol_middleware(request, call_next):
        if request.headers.get("x-forwarded-proto") == "https":
            request.scope["scheme"] = "https"
        return await call_next(request)

    # Mount routers with API key authentication
    app.include_router(
        chat.router, prefix=settings.API_PREFIX, dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        retrieval.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)],
    )
    app.include_router(
        chat_history.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)],
    )
    app.include_router(
        speech_to_text.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)],
    )
    app.include_router(
        memory.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)],
    )
    app.include_router(
        admin.router, prefix=settings.API_PREFIX, dependencies=[Depends(verify_api_key)]
    )
    # Health check router (no authentication required)
    app.include_router(
        health.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)],
    )
    # Auth routers without API prefix
    app.include_router(auth.router, prefix=settings.API_PREFIX)
    app.include_router(payme.router, prefix=settings.API_PREFIX)

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
