import hashlib
import hmac
import time

from src.models.auth import TelegramAuth, TelegramDataError, TelegramDataIsOutdated


def validate_telegram_data(telegram_bot_token: str, data: TelegramAuth) -> dict:
    """
    Validate Telegram authentication data according to official documentation.
    Official telegram doc: https://core.telegram.org/widgets/login

    Returns validated user data if successful.
    Raises exception if validation fails.
    """
    data_dict = data.model_dump()

    # Filter out None values before processing
    filtered_data = {k: v for k, v in data_dict.items() if v is not None}

    received_hash = filtered_data.pop("hash", None)

    auth_date = filtered_data.get("auth_date")

    # Check if session is expired (configurable timeout)
    if _verify_telegram_session_outdate(auth_date):
        raise TelegramDataIsOutdated("Telegram authentication session is expired.")

    # Validate data integrity using HMAC-SHA256
    generated_hash = _generate_hash(filtered_data, telegram_bot_token)

    if generated_hash != received_hash:
        raise TelegramDataError("Request data is incorrect")

    return filtered_data


def _verify_telegram_session_outdate(auth_date: str) -> bool:
    """Check if Telegram auth session is expired (24 hours)"""
    one_day_in_seconds = 86400
    unix_time_now = int(time.time())
    unix_time_auth_date = int(auth_date)
    timedelta = unix_time_now - unix_time_auth_date

    return timedelta > one_day_in_seconds


def _generate_hash(data: dict, token: str) -> str:
    """Generate HMAC-SHA256 hash for Telegram data validation"""
    request_data_alph_sorted = sorted(data.items(), key=lambda v: v[0])

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in request_data_alph_sorted
    )

    secret_key = hashlib.sha256(token.encode()).digest()
    generated_hash = hmac.new(
        key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256
    ).hexdigest()

    return generated_hash
