import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from core.config import settings
from models.auth import JWTPayload


class JWTService:
    """Verify short-lived JWTs signed by the authenticated frontend server."""

    def __init__(self) -> None:
        self.secret = settings.JWT_SECRET_KEY
        if not self.secret or len(self.secret) < 32:
            raise RuntimeError("JWT_SECRET_KEY must contain at least 32 characters")
        self.algorithm = settings.JWT_ALGORITHM
        if self.algorithm not in {"HS256", "HS384", "HS512"}:
            raise RuntimeError("JWT_ALGORITHM must be HS256, HS384, or HS512")
        self.issuer = settings.JWT_ISSUER
        self.audience = settings.JWT_AUDIENCE

    def verify(self, token: str) -> JWTPayload:
        if not token or len(token) > 4096:
            raise InvalidTokenError("Invalid token")

        payload = jwt.decode(
            token,
            self.secret,
            algorithms=[self.algorithm],
            audience=self.audience,
            issuer=self.issuer,
            options={
                "require": ["sub", "exp", "iat", "jti", "typ", "iss", "aud"]
            },
        )
        try:
            return JWTPayload.model_validate(payload)
        except ValidationError as exc:
            raise InvalidTokenError("Invalid access token claims") from exc
