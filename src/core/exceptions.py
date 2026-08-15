from fastapi import HTTPException, status

from core.error_codes import ErrorCode


class ChatException(HTTPException):
    """Base exception for chat-related errors.

    `code`/`params` are optional so existing bare-string callers keep
    working; the normalizing handler in `core.error_handlers` falls back to
    a status-derived code when they're absent. New raise sites should always
    pass `code`, and `params` when the frontend needs structured values
    (e.g. a retry-after seconds, a remaining-credits count).
    """

    def __init__(
        self,
        detail: str,
        status_code: int,
        code: ErrorCode | None = None,
        params: dict | None = None,
    ):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.params = params or {}


class QueryValidationException(ChatException):
    """Raised when query validation fails."""

    def __init__(self, detail: str, code: ErrorCode | None = None, params: dict | None = None):
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST, code=code, params=params)


class QueryTooLongException(QueryValidationException):
    """Raised when query exceeds maximum allowed length."""

    def __init__(self, query_length: int, max_length: int):
        detail = (
            f"Query too long. Maximum {max_length} characters allowed, "
            f"got {query_length} characters."
        )
        super().__init__(
            detail=detail,
            code=ErrorCode.QUERY_TOO_LONG,
            params={"length": query_length, "max_length": max_length},
        )


class InsufficientCreditsException(ChatException):
    """Raised when user has insufficient credits."""

    def __init__(self, credits_remaining: int, limit: int, required_credits: int = 1):
        detail = (
            f"Insufficient credits. You have {credits_remaining}/{limit} credits remaining. "
            f"This request requires {required_credits} credit(s)."
        )
        super().__init__(
            detail=detail,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=ErrorCode.CREDITS_EXHAUSTED,
            params={"remaining": credits_remaining, "limit": limit, "required": required_credits},
        )


class ChatGenerationException(ChatException):
    """Raised when answer generation fails."""

    def __init__(
        self, detail: str = "Failed to generate answer. Please try again later."
    ):
        super().__init__(
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=ErrorCode.CHAT_GENERATION_FAILED,
        )


class FlowExecutionException(ChatException):
    """Raised when agentic RAG flow execution fails."""

    def __init__(
        self, detail: str = "Failed to execute RAG flow. Please try again later."
    ):
        super().__init__(
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=ErrorCode.FLOW_EXECUTION_FAILED,
        )


class AssistantConfigException(ChatException):
    """Raised when assistant configuration is invalid."""

    def __init__(self, assistant_name: str):
        detail = f"Invalid assistant configuration: {assistant_name}"
        super().__init__(
            detail=detail,
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.ASSISTANT_CONFIG_INVALID,
            params={"assistant_name": assistant_name},
        )


class StreamingException(ChatException):
    """Raised when streaming response fails."""

    def __init__(self, detail: str = "Streaming response failed. Please try again."):
        super().__init__(
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=ErrorCode.STREAMING_FAILED,
        )


class TooLongFileContentException(ChatException):
    """Raised when the content of the uploaded file is too long."""

    def __init__(self, file_name: str, content_length: int, max_length: int):
        detail = (
            f"Content of the file '{file_name}' is too long. "
            f"Maximum {max_length} characters allowed, got {content_length} characters."
        )
        super().__init__(
            detail=detail,
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.FILE_CONTENT_TOO_LONG,
            params={"file_name": file_name, "length": content_length, "max_length": max_length},
        )


# Chat History Service Exceptions
class ChatHistoryException(ChatException):
    """Base exception for chat history service errors."""

    def __init__(
        self,
        detail: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: ErrorCode | None = None,
        params: dict | None = None,
    ):
        super().__init__(detail=detail, status_code=status_code, code=code, params=params)


class UserNotFoundError(ChatHistoryException):
    """Raised when a user cannot be found in the database."""

    def __init__(self, user_id: str):
        super().__init__(
            detail=f"User with ID '{user_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.USER_NOT_FOUND,
            params={"user_id": user_id},
        )


class SessionNotFoundError(ChatHistoryException):
    """Raised when a session cannot be found in the database."""

    def __init__(self, session_id: str):
        super().__init__(
            detail=f"Session with ID '{session_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.SESSION_NOT_FOUND,
            params={"session_id": session_id},
        )


class MessageNotFoundError(ChatHistoryException):
    """Raised when a message cannot be found in the database."""

    def __init__(self, message_id: str):
        super().__init__(
            detail=f"Message with ID '{message_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.MESSAGE_NOT_FOUND,
            params={"message_id": message_id},
        )


class InvalidInputError(ChatHistoryException):
    """Raised when input validation fails."""

    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST, code=ErrorCode.BAD_REQUEST)


class UserBlockedError(ChatHistoryException):
    """Raised when a user is blocked."""

    def __init__(self, user_id: str):
        super().__init__(
            detail=f"User with ID '{user_id}' is blocked and cannot perform this action.",
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.USER_BLOCKED,
            params={"user_id": user_id},
        )


class UserAlreadyExistsException(ChatHistoryException):
    """Raised when a user already exists (duplicate)."""

    def __init__(self, external_id: str):
        super().__init__(
            detail=f"User with external ID '{external_id}' already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.USER_ALREADY_EXISTS,
            params={"external_id": external_id},
        )


# OTP / Twilio Verify Exceptions
class OTPException(ChatException):
    """Base exception for OTP-related errors."""

    def __init__(
        self,
        detail: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: ErrorCode | None = None,
        params: dict | None = None,
    ):
        super().__init__(detail=detail, status_code=status_code, code=code, params=params)


class InvalidPhoneFormatException(OTPException):
    """Raised when the supplied phone number is not valid E.164."""

    def __init__(self, phone: str):
        super().__init__(
            detail=(
                f"Invalid phone number format: '{phone}'. "
                "Expected E.164 (e.g. +998901234567)."
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.INVALID_PHONE_FORMAT,
            params={"phone": phone},
        )


class OTPSendFailedException(OTPException):
    """Raised when Twilio rejects or fails to send the verification."""

    def __init__(self, detail: str = "Failed to send OTP. Please try again later."):
        super().__init__(
            detail=detail,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=ErrorCode.OTP_SEND_FAILED,
        )


class OTPVerificationFailedException(OTPException):
    """Raised when the submitted OTP code is invalid, expired, or otherwise not approved."""

    def __init__(self, twilio_status: str | None = None):
        detail = "Invalid or expired OTP code."
        if twilio_status and twilio_status != "pending":
            detail = f"OTP verification {twilio_status}."
        super().__init__(
            detail=detail,
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.OTP_INVALID_OR_EXPIRED,
            params={"twilio_status": twilio_status} if twilio_status else {},
        )


class OTPRateLimitExceededException(OTPException):
    """Raised when a phone exceeds the per-window send or verify cap."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(
            detail=(
                "Too many OTP requests for this phone number. "
                f"Try again in {retry_after_seconds} seconds."
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=ErrorCode.OTP_RATE_LIMITED,
            params={"retry_after_seconds": retry_after_seconds},
        )
        self.retry_after_seconds = retry_after_seconds


class OTPServiceNotConfiguredException(OTPException):
    """Raised when Twilio credentials are missing at runtime."""

    def __init__(self):
        super().__init__(
            detail="OTP service is not configured.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.OTP_SERVICE_UNAVAILABLE,
        )
