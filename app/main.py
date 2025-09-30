# app/main.py
import secrets

# FastAPI imports
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Depends, Security
from fastapi.security import HTTPBasic, HTTPBasicCredentials, APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi

# Internal imports
from app.api import chat, retrieval, chat_history, count, memory
from app.core.logger import logger
from app.core.config import settings


security = HTTPBasic()

# API Key authentication
api_key_header = APIKeyHeader(
    name=settings.API_KEY_NAME.lower(),  # make sure it matches lowercase
    auto_error=False,
    description="HBAI API Key"
)

def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.DOCS_USER)
    correct_password = secrets.compare_digest(credentials.password, settings.DOCS_PASSWORD)
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
    # Startup
    port = 8080  # This should match the port in the Dockerfile
    logger.info(f"WakilAI API running at http://localhost:{port} and http://0.0.0.0:{port}")
    logger.info(f"API documentation available at http://localhost:{port}/docs")
    yield
    # Shutdown (if needed)
    pass

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
    
    # Mount routers with API key authentication
    app.include_router(
        chat.router, 
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        retrieval.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        chat_history.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        count.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        memory.router,
        prefix=settings.API_PREFIX,
        dependencies=[Depends(verify_api_key)]
    )
    
    # Health Check Route (no authentication required)
    @app.get("/", tags=["Health"])
    async def health_check():
        return {"status": "ok", "message": "WakilAI API is running 🚀"}
    
    # Documentation endpoints (Basic Auth protected)
    @app.get("/docs")
    async def get_documentation(username: str = Depends(get_current_username)):
        return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")
    
    @app.get("/redoc")
    async def get_documentation(username: str = Depends(get_current_username)):
        return get_redoc_html(openapi_url="/openapi.json", title="redocs")

    @app.get("/swagger-ui.html")
    async def get_documentation(username: str = Depends(get_current_username)):
        return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")

    @app.get("/openapi.json")
    async def openapi(username: str = Depends(get_current_username)):
        return get_openapi(title = "FastAPI", version="0.1.0", routes=app.routes)

    return app

app = create_app()