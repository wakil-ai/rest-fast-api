import time
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken, KeySet
from authlib.jose.errors import JoseError

TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_AUTH_URL = f"{TELEGRAM_ISSUER}/auth"
TELEGRAM_TOKEN_URL = f"{TELEGRAM_ISSUER}/token"
TELEGRAM_JWKS_URL = f"{TELEGRAM_ISSUER}/.well-known/jwks.json"

# Signing algorithms advertised by Telegram's OIDC discovery document.
TELEGRAM_ID_TOKEN_ALGS = ["RS256", "ES256", "EdDSA", "ES256K"]

_KEY_SET_CACHE: KeySet | None = None
_KEY_SET_CACHE_AT: float = 0.0
_KEY_SET_CACHE_TTL_SECONDS = 3600


async def _get_telegram_key_set() -> KeySet:
    global _KEY_SET_CACHE, _KEY_SET_CACHE_AT

    now = time.time()
    cached = _KEY_SET_CACHE
    if cached is not None and (now - _KEY_SET_CACHE_AT) < _KEY_SET_CACHE_TTL_SECONDS:
        return cached

    async with httpx.AsyncClient() as client:
        response = await client.get(TELEGRAM_JWKS_URL, timeout=10.0)
        response.raise_for_status()
        key_set = JsonWebKey.import_key_set(response.json())

    _KEY_SET_CACHE = key_set
    _KEY_SET_CACHE_AT = now
    return key_set


async def verify_telegram_id_token(id_token: str, client_id: str) -> dict[str, Any]:
    """
    Verify a Telegram OpenID Connect ID token (JWT).

    Validates signature against Telegram's JWKS, plus issuer, audience, and expiry.
    Returns the validated claims on success; raises ValueError otherwise.
    """
    if not id_token:
        raise ValueError("Missing id_token")
    if not client_id:
        raise ValueError("Missing client_id for audience check")

    key_set = await _get_telegram_key_set()
    jwt = JsonWebToken(TELEGRAM_ID_TOKEN_ALGS)

    try:
        claims = jwt.decode(
            id_token,
            key_set,
            claims_options={
                "iss": {"essential": True, "value": TELEGRAM_ISSUER},
                "aud": {"essential": True, "value": str(client_id)},
                "exp": {"essential": True},
                "sub": {"essential": True},
            },
        )
        claims.validate()
    except JoseError as exc:
        raise ValueError(f"Invalid Telegram ID token: {exc}") from exc

    return dict(claims)


async def exchange_telegram_oauth_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """
    Exchange an authorization code for tokens at Telegram's token endpoint.

    Uses client_secret_basic authentication (per Telegram's discovery document).
    """
    if not client_id or not client_secret:
        raise ValueError("Telegram OAuth credentials are not configured")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_id,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            TELEGRAM_TOKEN_URL,
            data=data,
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
            timeout=20.0,
        )
        if response.status_code != 200:
            raise ValueError(
                f"Telegram token exchange failed ({response.status_code}): {response.text}"
            )
        return response.json()
