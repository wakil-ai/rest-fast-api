"""ATMOS callback authentication: separate payment/bind keys, fail closed when unset."""

import hashlib

import pytest

from core.config import settings
from services.payments.atmos import AtmosService

PAYMENT_KEY = "test-payment-key"
BIND_KEY = "test-bind-key"
FIELDS = {"store_id": "11069", "transaction_id": "257280", "invoice": "1281539", "amount": "100000"}


def _service() -> AtmosService:
    # The key checks run before any DB or HTTP access, so skip __init__.
    return AtmosService.__new__(AtmosService)


def _sign(key: str) -> str:
    raw = f"{FIELDS['store_id']}{FIELDS['transaction_id']}{FIELDS['invoice']}{FIELDS['amount']}{key}"
    return hashlib.md5(raw.encode()).hexdigest()


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ATMOS_PAYMENT_CALLBACK_API_KEY", PAYMENT_KEY)
    monkeypatch.setattr(settings, "ATMOS_BIND_CALLBACK_API_KEY", BIND_KEY)


def test_payment_sign_uses_payment_key(keys) -> None:
    assert _service()._verify_payment_sign(**FIELDS, sign=_sign(PAYMENT_KEY))


def test_payment_sign_rejects_bind_key(keys) -> None:
    assert not _service()._verify_payment_sign(**FIELDS, sign=_sign(BIND_KEY))


def test_payment_sign_fails_closed_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ATMOS_PAYMENT_CALLBACK_API_KEY", None)
    # A sign computed with an empty key must not pass.
    assert not _service()._verify_payment_sign(**FIELDS, sign=_sign(""))


async def test_bind_callback_rejects_payment_key(keys) -> None:
    result = await _service().handle_bind_callback({"api_key": PAYMENT_KEY, "card_id": 1})
    assert result == {"status": 0, "message": "Invalid api_key"}


async def test_bind_callback_fails_closed_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ATMOS_BIND_CALLBACK_API_KEY", None)
    result = await _service().handle_bind_callback({"api_key": "", "card_id": 1})
    assert result == {"status": 0, "message": "Invalid api_key"}
    result = await _service().handle_bind_callback({"api_key": "anything", "card_id": 1})
    assert result == {"status": 0, "message": "Invalid api_key"}
