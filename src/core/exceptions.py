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


# Chat History Service Exceptions
class ChatHistoryException(ChatException):
    """Base exception for chat history service errors."""

    def __init__(self, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(detail=detail, status_code=status_code)


class UserNotFoundError(ChatHistoryException):
    """Raised when a user cannot be found in the database."""

    def __init__(self, user_id: str):
        super().__init__(
            detail=f"User with ID '{user_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class SessionNotFoundError(ChatHistoryException):
    """Raised when a session cannot be found in the database."""

    def __init__(self, session_id: str):
        super().__init__(
            detail=f"Session with ID '{session_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class MessageNotFoundError(ChatHistoryException):
    """Raised when a message cannot be found in the database."""

    def __init__(self, message_id: str):
        super().__init__(
            detail=f"Message with ID '{message_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class InvalidInputError(ChatHistoryException):
    """Raised when input validation fails."""

    def __init__(self, detail: str):
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST)


class UserBlockedError(ChatHistoryException):
    """Raised when a user is blocked."""

    def __init__(self, user_id: str):
        super().__init__(
            detail=f"User with ID '{user_id}' is blocked and cannot perform this action.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


class UserAlreadyExistsException(ChatHistoryException):
    """Raised when a user already exists (duplicate)."""

    def __init__(self, external_id: str):
        super().__init__(
            detail=f"User with external ID '{external_id}' already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class PhoneNumberAlreadyExistsError(ChatHistoryException):
    """Raised when a phone number is already in use by another user."""

    def __init__(self) -> None:
        super().__init__(
            detail="This phone number is already in use by another account.",
            status_code=status.HTTP_409_CONFLICT,
        )


# OTP / Twilio Verify Exceptions
class OTPException(ChatException):
    """Base exception for OTP-related errors."""

    def __init__(self, detail: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(detail=detail, status_code=status_code)


class InvalidPhoneFormatException(OTPException):
    """Raised when the supplied phone number is not valid E.164."""

    def __init__(self, phone: str):
        super().__init__(
            detail=(
                f"Invalid phone number format: '{phone}'. "
                "Expected E.164 (e.g. +998901234567)."
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class OTPSendFailedException(OTPException):
    """Raised when Twilio rejects or fails to send the verification."""

    def __init__(self, detail: str = "Failed to send OTP. Please try again later."):
        super().__init__(
            detail=detail, status_code=status.HTTP_502_BAD_GATEWAY
        )


class OTPVerificationFailedException(OTPException):
    """Raised when the submitted OTP code is invalid, expired, or otherwise not approved."""

    def __init__(self, twilio_status: str | None = None):
        detail = "Invalid or expired OTP code."
        if twilio_status and twilio_status != "pending":
            detail = f"OTP verification {twilio_status}."
        super().__init__(detail=detail, status_code=status.HTTP_400_BAD_REQUEST)


class OTPRateLimitExceededException(OTPException):
    """Raised when a phone exceeds the per-window send or verify cap."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(
            detail=(
                "Too many OTP requests for this phone number. "
                f"Try again in {retry_after_seconds} seconds."
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        self.retry_after_seconds = retry_after_seconds


class OTPServiceNotConfiguredException(OTPException):
    """Raised when Twilio credentials are missing at runtime."""

    def __init__(self):
        super().__init__(
            detail="OTP service is not configured.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
