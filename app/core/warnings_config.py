"""Register third-party warning filters before heavy dependency imports."""

from __future__ import annotations

import os
import warnings


def configure_startup_warnings() -> None:
    """Silence known third-party startup noise we cannot fix in vendor code."""
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

    from langchain_core._api.deprecation import (
        LangChainDeprecationWarning,
        LangChainPendingDeprecationWarning,
    )

    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
    warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)
    warnings.filterwarnings(
        "ignore",
        message="You are using a Python version",
        category=FutureWarning,
        module="google.api_core._python_version_support",
    )
    warnings.filterwarnings(
        "ignore",
        message="Couldn't find ffmpeg or avconv",
        category=RuntimeWarning,
        module="pydub.utils",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"get_async_redis_connection will become async",
        category=DeprecationWarning,
    )
