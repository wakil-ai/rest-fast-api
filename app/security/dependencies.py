import base64
import secrets

from fastapi import Depends, HTTPException, Request, Security, status
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


# DT Team API Key
dt_team_key_header = APIKeyHeader(
    name=settings.DT_API_KEY_NAME.lower(),
    auto_error=False,
    description="DT Team API Key — required for DT team backend access",
)


def verify_dt_api_key(
    api_key: str = Security(dt_team_key_header), request: Request = None
) -> bool:
    """Verify the DT team API key AND check if request is from DT server IP.
    
    Both API key and source IP must be valid.
    """
    # Verify API key
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DT Team API Key required",
        )
    if settings.DT_API_KEY is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DT Team API Key not configured",
        )
    if not secrets.compare_digest(api_key, settings.DT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid DT Team API Key",
        )
    
    # Verify source IP if request object is available
    if request:
        client_ip = request.client.host if request.client else None
        
        if not client_ip:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unable to determine client IP address",
            )
        
        if client_ip != settings.DT_SERVER_IP:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. DT Team API Key can only be used specified ip address, request from {client_ip}",
            )
    
    return True


def verify_api_key_or_dt_key(request: Request) -> bool:
    """Verify either default API key (any IP) OR DT team key (restricted to DT_SERVER_IP).
    
    Logic:
    - If request has default API_KEY header: Allow from any IP
    - If request has DT_TEAM_API_KEY header: Require DT_SERVER_IP
    - If neither: Reject with 401
    """
    default_api_key = request.headers.get(settings.API_KEY_NAME.lower())
    dt_team_api_key = request.headers.get(settings.DT_API_KEY_NAME.lower())
    
    # Check if default API key is provided
    if default_api_key:
        if secrets.compare_digest(default_api_key, settings.API_KEY):
            return True  # Allow from any IP
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API Key",
            )
    
    # Check if DT team API key is provided
    if dt_team_api_key:
        if settings.DT_API_KEY is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DT Team API Key not configured",
            )
        if not secrets.compare_digest(dt_team_api_key, settings.DT_API_KEY):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid DT Team API Key",
            )
        
        # Verify source IP for DT team key
        client_ip = request.client.host if request.client else None
        if not client_ip:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unable to determine client IP address",
            )
        
        if client_ip != settings.DT_SERVER_IP:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. DT Team API Key can only be used from {settings.DT_SERVER_IP}, request from {client_ip}",
            )
        return True
    
    # Neither key provided
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API Key required (use either admin or x-dt-team-api-key header)",
    )


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