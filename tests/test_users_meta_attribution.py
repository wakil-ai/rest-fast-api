"""POST /users: 201 only on a real signup, and ad attribution is stored safely."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Response

from api.v2.history import users as users_module
from models.chat_history import UserCreateRequest

_IDFA = "6D92078A-8246-4BA4-AE5B-76104861E7DC"
_AD = {"platform": "ios", "madid": _IDFA, "anon_id": "anon-1", "att": 1, "os_version": "17.4"}


@pytest.fixture
def service(monkeypatch):
    svc = MagicMock()
    svc.get_user = AsyncMock(return_value=None)
    svc.create_user_with_status = AsyncMock(return_value=({"_id": "u1"}, True))
    svc.set_ad_attribution = AsyncMock()
    monkeypatch.setattr(users_module, "chat_history_service", svc)
    monkeypatch.setattr(users_module, "invalidate_user_auth_cache", lambda _uid: None)
    return svc


async def _call(**fields):
    response = Response()
    body = await users_module.create_or_get_user(UserCreateRequest(user_id="u1", **fields), response)
    return response, body


async def test_new_user_answers_201_and_stores_attribution(service):
    response, body = await _call(**_AD)
    assert response.status_code == 201
    assert body["message"] == "User created successfully"
    service.set_ad_attribution.assert_awaited_once()
    saved = service.set_ad_attribution.await_args.args[1]
    assert saved["madid"] == _IDFA and saved["os_version"] == "17.4"


async def test_lost_signup_race_does_not_answer_201(service):
    service.create_user_with_status.return_value = ({"_id": "u1"}, False)
    response, body = await _call(**_AD)
    assert response.status_code != 201
    assert body["message"] == "User already exists"


async def test_existing_user_with_changed_attribution_is_updated(service):
    service.get_user.return_value = {"_id": "u1", "madid": "", "att": 0}
    response, _ = await _call(**_AD)
    assert response.status_code != 201
    service.set_ad_attribution.assert_awaited_once()


async def test_existing_user_with_same_attribution_is_not_rewritten(service):
    service.get_user.return_value = {"_id": "u1", **_AD}
    await _call(**_AD)
    service.set_ad_attribution.assert_not_awaited()


async def test_archived_user_never_gets_ad_ids_written_back(service):
    service.get_user.return_value = {"_id": "u1", "archived": True}
    await _call(**_AD)
    service.set_ad_attribution.assert_not_awaited()


async def test_malformed_attribution_is_dropped_not_fatal(service):
    response, _ = await _call(att="1a", platform=5, madid=["x"])
    assert response.status_code == 201
    service.set_ad_attribution.assert_not_awaited()


async def test_attribution_write_failure_never_fails_the_login(service):
    service.set_ad_attribution.side_effect = RuntimeError("mongo down")
    response, _ = await _call(**_AD)
    assert response.status_code == 201
