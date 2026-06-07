"""Langfuse observability helpers for the public backend."""

from __future__ import annotations

import os

from app.core.config import settings
from app.core.logger import logger

_langfuse_env_configured = False


def langfuse_base_url() -> str | None:
    explicit = (settings.LANGFUSE_BASE_URL or "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = (settings.LANGFUSE_HOST or "").strip()
    if host:
        return host.rstrip("/")
    return None


def is_langfuse_enabled() -> bool:
    if not settings.LANGFUSE_TRACING_ENABLED:
        return False
    return bool(
        (settings.LANGFUSE_PUBLIC_KEY or "").strip()
        and (settings.LANGFUSE_SECRET_KEY or "").strip()
        and langfuse_base_url()
    )


def configure_langfuse_env() -> None:
    """Map app settings to Langfuse SDK environment variables."""
    global _langfuse_env_configured
    if _langfuse_env_configured:
        return
    _langfuse_env_configured = True
    if not is_langfuse_enabled():
        logger.info(
            "Langfuse tracing disabled (missing keys, host, or LANGFUSE_TRACING_ENABLED=false)"
        )
        return

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY.strip())
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY.strip())
    base = langfuse_base_url()
    if base:
        os.environ.setdefault("LANGFUSE_BASE_URL", base)
    logger.info("Langfuse tracing enabled (host={})", base)


def flush_langfuse() -> None:
    if not is_langfuse_enabled():
        return
    configure_langfuse_env()
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:
        logger.warning("Langfuse flush failed: {}", exc)


def flush_langfuse_if_enabled() -> None:
    """Push batched observations to Langfuse when tracing is configured."""
    flush_langfuse()


__all__ = [
    "configure_langfuse_env",
    "flush_langfuse",
    "flush_langfuse_if_enabled",
    "is_langfuse_enabled",
    "langfuse_base_url",
]
