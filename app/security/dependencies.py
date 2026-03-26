import base64
import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

from app.core.config import settings

# HTTP Basic (docs)
security = HTTPBasic()


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


# Regular API Keys
api_key_header = APIKeyHeader(
    name=settings.API_KEY_NAME.lower(),
    auto_error=False,
    description="HBAI API Key",
)


def verify_api_key(api_key: str = Security(api_key_header)):
    """Verify API key authentication."""
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not secrets.compare_digest(api_key, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return True


# Super Admin API Key
super_admin_key_header = APIKeyHeader(
    name=settings.SUPER_ADMIN_KEY_NAME.lower(),
    auto_error=False,
    description="Super Admin API Key — required for logs and sensitive admin endpoints",
)


def verify_super_admin_key(api_key: str = Security(super_admin_key_header)):
    """Verify the super-admin API key (separate from the regular API key)."""
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Super Admin API Key required",
        )
    if not secrets.compare_digest(api_key, settings.SUPER_ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Super Admin API Key",
        )
    return True


def verify_payme_authorization(authorization: str | None) -> bool:
    """Verify Payme authorization header"""
    if not authorization:
        return False

    try:
        # Extract credentials from "Basic base64string"
        auth_type, credentials = authorization.split(" ", 1)
        if auth_type.lower() != "basic":
            return False

        # Decode base64 credentials
        decoded = base64.b64decode(credentials).decode("utf-8")
        # Should be in format "Paycom:merchant_key"
        username, password = decoded.split(":", 1)

        if username != "Paycom":
            return False

        # Compare with configured merchant key
        if not secrets.compare_digest(password, settings.PAYME_MERCHANT_KEY):
            return False

        return True

    except Exception:
        return False
