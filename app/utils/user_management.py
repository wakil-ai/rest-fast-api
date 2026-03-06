import random
import string
from functools import wraps
from typing import Any

from fastapi import HTTPException, status

from app.core.logger import logger


def generate_short_id(prefix: str = "", length: int = 8, type: str = "random") -> str:
    """Generate a random short ID with an optional prefix."""
    if type not in ["random", "uuid", "hash"]:
        raise ValueError(
            "Invalid type for ID generation. Supported types: 'random', 'uuid', 'hash'."
        )

    unique_id = ""

    if type == "random":
        chars = string.ascii_lowercase + string.digits
        unique_id = "".join(random.choice(chars) for _ in range(length))

    elif type == "uuid":
        import uuid

        unique_id = str(uuid.uuid4())[:length]

    elif type == "hash":
        import hashlib

        unique_id = hashlib.sha256(str(random.random()).encode()).hexdigest()[:length]

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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal Server Error",
            )

    return wrapper
