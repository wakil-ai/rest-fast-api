from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2.otp import router
from core.dependencies import get_otp_service


def test_otp_send_requires_jwt_not_service_key():
    fake = SimpleNamespace(send_otp=AsyncMock(return_value={'sid': 'old-sid', 'request_id': 'id', 'status': 'pending', 'channel': 'sms', 'expires_in': 300, 'resend_after': 60}))
    app = FastAPI()
    app.include_router(router, prefix='/api/v2')
    app.dependency_overrides[get_otp_service] = lambda: fake
    client = TestClient(app)
    response = client.post('/api/v2/auth/otp/send', json={'phone_number': '+998901234567', 'channel': 'sms', 'locale': 'uz'}, headers={'admin': 'admin'})
    assert response.status_code == 401
    fake.send_otp.assert_not_called()


def authenticated_headers(monkeypatch, user_id='user1'):
    import uuid
    from datetime import datetime, timedelta, timezone
    import jwt
    from core.config import settings
    from security import dependencies
    monkeypatch.setattr(settings, 'JWT_SECRET_KEY', 'test-jwt-secret-at-least-32-bytes')
    monkeypatch.setattr(dependencies, 'get_cached_user_auth_status', AsyncMock(return_value={'exists': True, 'is_blocked': False, 'archived': False}))
    now = datetime.now(timezone.utc)
    token = jwt.encode({'sub': user_id, 'iat': now, 'exp': now + timedelta(minutes=5), 'jti': uuid.uuid4().hex, 'typ': 'access_token', 'iss': settings.JWT_ISSUER, 'aud': settings.JWT_AUDIENCE}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return {'Authorization': 'Bearer ' + token}


def test_authenticated_send_verify_contract_binds_real_jwt_subject(monkeypatch):
    fake = SimpleNamespace(send_otp=AsyncMock(return_value={'sid': 'old-sid', 'request_id': 'id', 'status': 'pending', 'channel': 'sms', 'expires_in': 300, 'resend_after': 60}), verify_otp=AsyncMock(return_value={'status': 'approved', 'verification_token': 'proof'}))
    app = FastAPI()
    app.include_router(router, prefix='/api/v2')
    app.dependency_overrides[get_otp_service] = lambda: fake
    client = TestClient(app)
    headers = authenticated_headers(monkeypatch)
    response = client.post('/api/v2/auth/otp/send', json={'phone_number': '+998 90 123 45 67', 'channel': 'sms', 'locale': 'ru', 'user_id': 'attacker'}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {'success': True, 'request_id': 'id', 'status': 'pending', 'channel': 'sms', 'expires_in': 300, 'resend_after': 60}
    fake.send_otp.assert_awaited_once_with(phone_number='+998901234567', channel='sms', locale='ru', user_id='user1')
    response = client.post('/api/v2/auth/otp/verify', json={'phone_number': '+998901234567', 'code': '123456'}, headers=headers)
    assert response.json() == {'success': True, 'status': 'approved', 'phone_number': '+998901234567', 'verification_token': 'proof'}
    fake.verify_otp.assert_awaited_once_with(phone_number='+998901234567', code='123456', user_id='user1')
    assert client.post('/api/v2/auth/otp/verify', json={'phone_number': '+998901234567', 'code': '123456'}).status_code == 401


def test_phone_patch_requires_subject_bound_one_use_proof(monkeypatch):
    from api.v2.history import users
    from fakeredis import FakeRedis
    from services.otp_service import OTPService
    from core.config import settings
    import json
    service = OTPService(redis_client=FakeRedis(decode_responses=True), config=settings)
    service._redis.set(service._proof_key('proof'), json.dumps({'user_id': 'user1', 'phone_number': '+998901234567'}), ex=300)
    persistence = AsyncMock(return_value={'_id': 'user1', 'phone_number': '+998901234567', 'created_at': '2026-10-03T00:00:00Z', 'updated_at': '2026-10-03T00:00:00Z'})
    monkeypatch.setattr(users.chat_history_service, 'update_user_phone_number', persistence)
    monkeypatch.setattr(users.redis_service, 'invalidate_cache', lambda _: True)
    app = FastAPI()
    app.include_router(users.router, prefix='/api/v2/history')
    app.dependency_overrides[get_otp_service] = lambda: service
    client = TestClient(app)
    url = '/api/v2/history/users/phone-number'
    headers = authenticated_headers(monkeypatch)
    body = {'user_id': 'user1', 'phone_number': '+998 90 123 45 67', 'verification_token': 'proof'}
    assert client.patch(url, json=body).status_code == 401
    assert client.patch(url, json={**body, 'user_id': 'user2'}, headers=headers).status_code == 403
    assert client.patch(url, json={k: v for k, v in body.items() if k != 'verification_token'}, headers=headers).status_code == 422
    assert client.patch(url, json={**body, 'phone_number': '+998901234568'}, headers=headers).status_code == 400
    persistence.assert_not_awaited()
    assert client.patch(url, json=body, headers=headers).status_code == 200
    persistence.assert_awaited_once_with(user_id='user1', phone_number='+998901234567')
    assert client.patch(url, json=body, headers=headers).status_code == 400
    assert persistence.await_count == 1


def test_generic_profile_update_cannot_bypass_verified_phone_patch(monkeypatch):
    from api.v2.history import users
    persistence = AsyncMock(return_value={})
    monkeypatch.setattr(users.chat_history_service, 'update_user_info', persistence)
    app = FastAPI()
    app.include_router(users.router, prefix='/api/v2/history')
    client = TestClient(app)
    response = client.patch('/api/v2/history/users/change/info/user1', json={'field': 'phone_number', 'value': '+998901234567'}, headers=authenticated_headers(monkeypatch))
    assert response.status_code == 422
    persistence.assert_not_awaited()
