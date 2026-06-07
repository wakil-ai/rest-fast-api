"""
Backward compatibility: chat routes are implemented in ``api.v3.chat``.
Use ``POST /api/v3/chat/ask`` (recommended) or ``POST /api/v2/chat/ask`` — same behavior.
"""

from api.v3.chat import router

__all__ = ["router"]
