"""ATMOS response envelopes: both success dialects pass, business errors raise."""

import pytest

from models.payment import AtmosServiceError
from services.payments.atmos_client import AtmosClient

check = AtmosClient._check_envelope


def test_merchant_pay_ok_passes() -> None:
    data = {"result": {"code": "OK", "description": "Нет ошибок"}, "transaction_id": 1}
    assert check(data, path="/merchant/pay/create") is data


def test_card_bind_numeric_zero_passes() -> None:
    # Real ATMOS DEV response shape from /checkout/card-bind/create (2026-09-17).
    data = {
        "store_id": 11069,
        "payment_id": 7607,
        "url": "https://dev-checkout.atmos.uz/bind?id=x",
        "status": {"code": 0, "message": "Success", "trace_id": "t"},
    }
    assert check(data, path="/checkout/card-bind/create") is data


def test_business_error_raises_with_locale_message() -> None:
    data = {
        "status": {
            "code": "-5",
            "locale": {"uz": "Biznes jarayoni topilmadi", "ru": "Ненайден бизнес-процесс", "en": "Business process not found"},
            "description": "",
        }
    }
    with pytest.raises(AtmosServiceError) as exc:
        check(data, path="/checkout/card-bind/create")
    assert "-5" in str(exc.value)
    assert "Business process not found" in str(exc.value)
