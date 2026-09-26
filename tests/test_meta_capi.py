"""Meta CAPI: payload shape, attribution normalisation, pricing rules, failure isolation."""

from types import SimpleNamespace

import httpx
import pytest

from core.config import settings
from models.chat_history import UserCreateRequest
from services import meta_capi_service as capi
from services.payments.appstore import AppStoreService
from services.payments.playstore import GooglePlayService

_IDFA = "6D92078A-8246-4BA4-AE5B-76104861E7DC"
_USER = {
    "platform": "ios",
    "madid": _IDFA,
    "anon_id": "anon-1",
    "att": 1,
    "os_version": "17.4.1",
    "app_version": "1.2.0",
    "app_build": "45",
}


@pytest.fixture(autouse=True)
def _meta_settings(monkeypatch):
    monkeypatch.setattr(settings, "META_CAPI_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "META_DATASET_ID", "123")
    monkeypatch.setattr(settings, "META_CAPI_TEST_EVENT_CODE", None)
    monkeypatch.setattr(capi, "_RETRY_DELAYS", (0, 0))


def _event(user=_USER, **overrides):
    kwargs = {"user": user, "event_id": "o", "amount": 300_000, "event_time": 1}
    kwargs.update(overrides)
    return capi.build_purchase_event(**kwargs)


# --------------------------------------------------------------------------- #
# extinfo / event shape                                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform,version", [("ios", "i2"), ("android", "a2")])
def test_extinfo_has_16_items_with_required_slots_filled(platform, version):
    extinfo = capi.build_extinfo(
        platform, app_version="1.2.0", app_build="45", os_version="17.4.1"
    )
    assert len(extinfo) == 16
    assert extinfo[:5] == [version, "ai.humblebee.wakil", "1.2.0", "45", "17.4.1"]
    assert extinfo[5:] == [""] * 11


def test_purchase_event_shape_and_uzs_value_is_not_converted():
    event = _event(event_id="order-1", event_time=1700000000)
    assert event["action_source"] == "app"
    assert event["event_id"] == "order-1"
    assert event["user_data"] == {"madid": _IDFA, "anon_id": "anon-1"}
    assert event["app_data"]["advertiser_tracking_enabled"] == 1
    assert event["app_data"]["extinfo"][4] == "17.4.1"
    assert event["custom_data"] == {"value": 300_000.0, "currency": "UZS"}


def test_non_uzs_currency_is_reported_as_charged():
    event = _event(amount=9.99, currency="eur")
    assert event["custom_data"] == {"value": 9.99, "currency": "EUR"}


def test_android_att_is_always_enabled():
    user = {**_USER, "platform": "android", "madid": "", "att": 0}
    event = _event(user)
    assert event["app_data"]["advertiser_tracking_enabled"] == 1
    assert "madid" not in event["user_data"]
    assert event["app_data"]["extinfo"][0] == "a2"


@pytest.mark.parametrize(
    "user",
    [
        {},
        {"platform": "ios"},
        {"platform": "web", "anon_id": "a", "os_version": "1"},
        {**_USER, "os_version": ""},  # Meta requires the OS version
        {**_USER, "madid": "", "anon_id": ""},
    ],
)
def test_unattributable_users_are_skipped(user):
    assert _event(user) is None


@pytest.mark.parametrize("amount,currency", [(0, "UZS"), (None, "UZS"), (-5, "UZS"), (5, "??"), (0.001, "USD")])
def test_unpriced_amounts_are_skipped(amount, currency):
    assert _event(amount=amount, currency=currency) is None


# --------------------------------------------------------------------------- #
# normalisation / request model                                                #
# --------------------------------------------------------------------------- #
def _normalize(**kw):
    base = dict(platform=None, madid=None, anon_id=None, att=None)
    base.update(kw)
    return capi.normalize_ad_attribution(**base)


def test_normalize_returns_none_when_nothing_sent():
    assert _normalize() is None


def test_normalize_drops_bad_values_but_keeps_empty_madid():
    fields = _normalize(platform="IOS", madid="", anon_id="x" * 500, att=7)
    assert fields == {"platform": "ios", "madid": ""}


@pytest.mark.parametrize("madid", ["not-a-uuid", "6D92078A82464BA4AE5B76104861E7DC", 123, ["x"]])
def test_normalize_rejects_malformed_madid(madid):
    assert _normalize(madid=madid) is None


def test_normalize_treats_zero_advertising_id_as_no_id():
    assert _normalize(madid="00000000-0000-0000-0000-000000000000") == {"madid": ""}


@pytest.mark.parametrize("att,expected", [(0, 0), (1, 1), ("1", 1), (True, None), (1.0, None), (2, None), ("1a", None)])
def test_normalize_att(att, expected):
    fields = _normalize(att=att, platform="ios")
    assert fields.get("att") == expected


def test_normalize_keeps_device_versions():
    fields = _normalize(os_version=" 17.4.1 ", app_version="1.2.0", app_build="x" * 100)
    assert fields == {"os_version": "17.4.1", "app_version": "1.2.0"}


@pytest.mark.parametrize(
    "junk",
    [{"att": "1a"}, {"att": 1.5}, {"platform": 5}, {"madid": 123}, {"anon_id": ["x"]}, {"os_version": {}}],
)
def test_malformed_ad_fields_never_422_the_login(junk):
    UserCreateRequest(user_id="u", **junk)


# --------------------------------------------------------------------------- #
# sending                                                                      #
# --------------------------------------------------------------------------- #
class _FakeClient:
    """Stand-in for httpx.AsyncClient that replays a list of results (Response or Exception)."""

    calls: list[dict]

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json):
        self.calls.append({"url": url, "body": json})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(status, **body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x"))


async def test_send_purchase_swallows_http_errors(monkeypatch):
    client = _FakeClient([httpx.ConnectTimeout("t")] * 3)
    monkeypatch.setattr(capi.httpx, "AsyncClient", client)
    await capi.send_purchase(user=_USER, event_id="o", amount=1000)
    assert len(client.calls) == 3


async def test_send_purchase_retries_5xx_then_succeeds(monkeypatch):
    client = _FakeClient([_response(503), httpx.ReadTimeout("t"), _response(200, events_received=1)])
    monkeypatch.setattr(capi.httpx, "AsyncClient", client)
    await capi.send_purchase(user=_USER, event_id="o", amount=1000)
    assert len(client.calls) == 3


async def test_send_purchase_does_not_retry_client_errors(monkeypatch):
    client = _FakeClient([_response(400, error={"message": "bad", "code": 100})])
    monkeypatch.setattr(capi.httpx, "AsyncClient", client)
    await capi.send_purchase(user=_USER, event_id="o", amount=1000)
    assert len(client.calls) == 1


async def test_send_purchase_puts_token_in_body_not_url(monkeypatch):
    client = _FakeClient([_response(200, events_received=1)])
    monkeypatch.setattr(capi.httpx, "AsyncClient", client)
    await capi.send_purchase(user=_USER, event_id="o", amount=1000)
    call = client.calls[0]
    assert "secret-token" not in call["url"]
    assert call["body"]["access_token"] == "secret-token"
    assert call["url"].endswith(f"/{settings.META_GRAPH_API_VERSION}/123/events")


async def test_send_purchase_never_logs_the_response_body(monkeypatch):
    logged = []
    monkeypatch.setattr(capi.logger, "info", logged.append)
    monkeypatch.setattr(capi.logger, "error", logged.append)
    monkeypatch.setattr(
        capi.httpx, "AsyncClient", _FakeClient([_response(200, events_received=1, secret_echo="LEAK")])
    )
    await capi.send_purchase(user=_USER, event_id="o", amount=1000)
    assert logged and all("LEAK" not in line for line in logged)


async def test_send_purchase_noop_without_token(monkeypatch):
    monkeypatch.setattr(settings, "META_CAPI_TOKEN", None)
    monkeypatch.setattr(capi.httpx, "AsyncClient", lambda *a, **k: pytest.fail("called"))
    await capi.send_purchase(user=_USER, event_id="o", amount=1000)


async def test_disabled_reporting_warns_once_not_per_event(monkeypatch):
    monkeypatch.setattr(settings, "META_CAPI_TOKEN", None)
    monkeypatch.setattr(capi, "_warned_disabled", False)
    monkeypatch.setattr(capi.httpx, "AsyncClient", lambda *a, **k: pytest.fail("called"))
    warnings = []
    monkeypatch.setattr(capi.logger, "warning", warnings.append)
    for _ in range(3):
        await capi.send_purchase(user=_USER, event_id="o", amount=1000)
    assert len(warnings) == 1 and "META_CAPI_TOKEN" in warnings[0]


# --------------------------------------------------------------------------- #
# what each store reports                                                      #
# --------------------------------------------------------------------------- #
_QUOTE = {"amount_sum": 300_000}


def _apple(**fields):
    return SimpleNamespace(environment="Production", **fields)


def test_apple_reports_actual_price_in_store_currency():
    payload = _apple(price=9990, currency="USD")
    assert AppStoreService._meta_purchase_amount(payload, _QUOTE, "Production") == (9.99, "USD")


@pytest.mark.parametrize("env", ["Sandbox", "Xcode", "LocalTesting"])
def test_apple_test_environments_report_nothing(env):
    payload = SimpleNamespace(environment=env, price=9990, currency="USD")
    assert AppStoreService._meta_purchase_amount(payload, _QUOTE, env)[0] is None


def test_apple_environment_comes_from_the_signed_payload_not_the_client():
    payload = SimpleNamespace(environment="Sandbox", price=9990, currency="USD")
    assert AppStoreService._meta_purchase_amount(payload, _QUOTE, "Production")[0] is None


def test_apple_free_trial_reports_nothing():
    payload = _apple(price=0, currency="USD", offerType=1)
    amount, _ = AppStoreService._meta_purchase_amount(payload, _QUOTE, "Production")
    assert not amount


def test_apple_offer_without_price_reports_nothing_instead_of_catalog_price():
    payload = _apple(offerType=3)
    assert AppStoreService._meta_purchase_amount(payload, _QUOTE, "Production")[0] is None


def test_apple_without_price_or_offer_falls_back_to_catalog():
    assert AppStoreService._meta_purchase_amount(_apple(), _QUOTE, "Production") == (300_000, "UZS")


def _play_item(**fields):
    return {"autoRenewingPlan": {"recurringPrice": {"currencyCode": "UZS", "units": "129000", "nanos": 0}}, **fields}


def test_play_reports_recurring_price():
    amount, currency = GooglePlayService._meta_purchase_amount({}, _play_item(), _QUOTE)
    assert (amount, currency) == (129000, "UZS")


def test_play_test_purchase_reports_nothing():
    assert GooglePlayService._meta_purchase_amount({"testPurchase": {}}, _play_item(), _QUOTE)[0] is None


def test_play_offer_reports_nothing():
    item = _play_item(offerDetails={"basePlanId": "monthly", "offerId": "trial-7d"})
    assert GooglePlayService._meta_purchase_amount({}, item, _QUOTE)[0] is None


def test_play_without_price_falls_back_to_catalog():
    assert GooglePlayService._meta_purchase_amount({}, {}, _QUOTE) == (300_000, "UZS")
