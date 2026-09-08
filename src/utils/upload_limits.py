"""Per-scope upload limits (WK-267).

``scope`` matches the same string already stored on file records
(``FileManager``/``ChatHistoryService``): ``"message"`` for main-chat
attachments, ``"project"`` for a project's persistent file library.
"""

from core.config import settings

_BYTES_PER_MB = 1024 * 1024


def max_file_size_bytes(scope: str) -> int:
    max_mb = (
        settings.PROJECT_MAX_FILE_SIZE_MB
        if scope == "project"
        else settings.MAIN_CHAT_MAX_FILE_SIZE_MB
    )
    return max_mb * _BYTES_PER_MB


def max_file_size_mb(scope: str) -> int:
    return (
        settings.PROJECT_MAX_FILE_SIZE_MB
        if scope == "project"
        else settings.MAIN_CHAT_MAX_FILE_SIZE_MB
    )


def max_file_count(scope: str) -> int:
    return (
        settings.PROJECT_MAX_FILES
        if scope == "project"
        else settings.MAIN_CHAT_MAX_FILES
    )
