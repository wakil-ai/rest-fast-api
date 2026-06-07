"""Register third-party warning filters before dependency imports."""

from __future__ import annotations

import warnings


def configure_startup_warnings() -> None:
    """Silence known third-party startup noise we cannot fix in vendor code."""
    warnings.filterwarnings(
        "ignore",
        message="You are using a Python version",
        category=FutureWarning,
        module="google.api_core._python_version_support",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"Inheritance class AiohttpClientSession from ClientSession is discouraged",
        category=DeprecationWarning,
    )
