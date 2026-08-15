import hashlib
import random
import string
import uuid as uuid_module
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from functools import wraps
from typing import Any

import uuid6
from fastapi import HTTPException, status

from core.error_codes import ErrorCode
from core.exceptions import ChatException
from core.logger import logger


def generate_short_id(prefix: str = "", length: int = 8, type: str = "random") -> str:
    """Generate a random short ID with an optional prefix."""
    if type not in ["random", "uuid", "hash", "uuid7"]:
        raise ValueError(
            "Invalid type for ID generation. Supported types: 'random', 'uuid', 'hash', 'uuid7'."
        )

    unique_id = ""

    if type == "random":
        chars = string.ascii_lowercase + string.digits
        unique_id = "".join(random.choice(chars) for _ in range(length))

    elif type == "uuid":
        unique_id = str(uuid_module.uuid4())[:length]

    elif type == "hash":
        unique_id = hashlib.sha256(str(random.random()).encode()).hexdigest()[:length]

    elif type == "uuid7":
        unique_id = str(uuid6.uuid7())  # Convert UUID object to string

    if not unique_id:
        raise RuntimeError("Failed to generate short id")

    return f"{prefix}{unique_id}" if prefix else unique_id


def serialize_mongo_id(data: Any) -> Any:
    """Convert MongoDB ObjectId to string in a dict (or list of dicts)."""
    if isinstance(data, list):
        return [serialize_mongo_id(item) for item in data]
    if isinstance(data, dict) and "_id" in data:
        data["mongo_id"] = str(data["_id"])
        # We also convert _id to string in place if it's not already
        data["_id"] = str(data["_id"])
    return data


def sanitize_message_for_response(data: Any) -> Any:
    """Remove private message metadata fields from API responses."""
    if isinstance(data, list):
        return [sanitize_message_for_response(item) for item in data]

    if not isinstance(data, dict):
        return data

    sanitized = deepcopy(data)
    metadata = sanitized.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("model", None)

    return sanitized


def clean_for_mongodb(data: Any) -> Any:
    """Clean and convert data types that MongoDB cannot natively encode.

    Converts:
    - UUID objects to strings
    - Decimal objects to float
    - Other non-serializable types to strings
    """
    if isinstance(data, dict):
        return {k: clean_for_mongodb(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple)):
        return [clean_for_mongodb(item) for item in data]
    elif isinstance(data, uuid_module.UUID):
        return str(data)
    elif isinstance(data, Decimal):
        return float(data)
    elif isinstance(data, datetime):
        return data  # MongoDB handles datetime natively
    return data


def handle_service_error(func):
    """Decorator to handle service layer exceptions."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except HTTPException:
            # Preserve intended HTTP response codes from route handlers.
            raise
        except ValueError as e:
            logger.warning(f"Validation error in {func.__name__}: {str(e)}")
            raise ChatException(
                detail=str(e), status_code=status.HTTP_400_BAD_REQUEST, code=ErrorCode.BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            raise ChatException(
                detail="Internal Server Error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code=ErrorCode.INTERNAL_ERROR,
            )

    return wrapper
