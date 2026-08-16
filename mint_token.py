"""Mint a bearer token for a user_id, the way the Nuxt server does.

    uv run python mint_token.py user-019... [minutes]

Only the frontend mints; this backend just verifies (services/jwt_service.py:22).
Local use only — it needs JWT_SECRET_KEY from .env.
"""

import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")

import jwt
from core.config import settings

user_id = sys.argv[1]
minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 30
now = datetime.now(UTC)

print(
    jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=minutes),
            "jti": uuid.uuid4().hex,
            "typ": "access_token",  # models/auth.py:49 pins this literal
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
)
