from fastapi import HTTPException, status


class ChatException(HTTPException):
    """Base exception for chat-related errors."""

    def __init__(self, detail: str, status_code: int):
        super().__init__(status_code=status_code, detail=detail)


class QueryValidationException(ChatException):
    """Raised when query validation fails."""

    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST)


class QueryTooLongException(QueryValidationException):
    """Raised when query exceeds maximum allowed length."""

    def __init__(self, query_length: int, max_length: int):
        detail = (
            f"Query too long. Maximum {max_length} characters allowed, "
            f"got {query_length} characters."
        )
        super().__init__(detail=detail)


class InsufficientCreditsException(ChatException):
    """Raised when user has insufficient credits."""

    def __init__(self, credits_remaining: int, limit: int, required_credits: int = 1):
        detail = (
            f"Insufficient credits. You have {credits_remaining}/{limit} credits remaining. "
            f"This request requires {required_credits} credit(s)."
        )
        super().__init__(detail=detail, status_code=status.HTTP_429_TOO_MANY_REQUESTS)


class ChatGenerationException(ChatException):
    """Raised when answer generation fails."""

    def __init__(
        self, detail: str = "Failed to generate answer. Please try again later."
    ):
        super().__init__(
            detail=detail, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


class FlowExecutionException(ChatException):
    """Raised when agentic RAG flow execution fails."""

    def __init__(
        self, detail: str = "Failed to execute RAG flow. Please try again later."
    ):
        super().__init__(
            detail=detail, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


class AssistantConfigException(ChatException):
    """Raised when assistant configuration is invalid."""

    def __init__(self, assistant_name: str):
        detail = f"Invalid assistant configuration: {assistant_name}"
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST)


class StreamingException(ChatException):
    """Raised when streaming response fails."""

    def __init__(self, detail: str = "Streaming response failed. Please try again."):
        super().__init__(
            detail=detail, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


class TooLongFileContentException(ChatException):
    """Raised when the content of the uploaded file is too long."""

    def __init__(self, file_name: str, content_length: int, max_length: int):
        detail = (
            f"Content of the file '{file_name}' is too long. "
            f"Maximum {max_length} characters allowed, got {content_length} characters."
        )
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST)
