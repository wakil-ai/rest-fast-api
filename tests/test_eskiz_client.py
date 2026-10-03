import httpx
import pytest


@pytest.mark.asyncio
async def test_login_and_sms_use_official_multipart_contract():
    from services.eskiz_client import EskizClient

    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path == '/api/auth/login':
            assert b'name="email"' in request.content
            assert b'name="password"' in request.content
            return httpx.Response(200, json={'data': {'token': 'test-token'}})
        assert request.headers['authorization'] == 'Bearer test-token'
        assert request.url.path == '/api/message/sms/send'
        assert b'name="mobile_phone"' in request.content
        assert b'998901234567' in request.content
        assert b'name="from"' in request.content
        return httpx.Response(200, json={'id': 'provider-id', 'status': 'waiting'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = EskizClient(email='test@example.invalid', password='test-only', sender_id='4546', base_url='https://notify.eskiz.uz', timeout_seconds=5, http_client=http)
        assert await client.send_sms('+998901234567', '123456', 'uz') == 'provider-id'
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('refresh_status', [200, 401])
async def test_unauthorized_sms_has_one_bounded_refresh_or_relogin(refresh_status):
    from services.eskiz_client import EskizClient
    paths = []
    sends = 0
    def handle(request):
        nonlocal sends
        paths.append((request.method, request.url.path))
        if request.url.path.endswith('login'):
            return httpx.Response(200, json={'data': {'token': 'login-token'}})
        if request.url.path.endswith('refresh'):
            return httpx.Response(refresh_status, json={'data': {'token': 'new-token'}})
        sends += 1
        return httpx.Response(401 if sends == 1 else 200, json={'id': 'accepted', 'status': 'waiting'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = EskizClient(email='test', password='test', sender_id='4546', base_url='https://notify.eskiz.uz', timeout_seconds=5, http_client=http)
        assert await client.send_sms('+998901234567', '123456') == 'accepted'
    assert ('PATCH', '/api/auth/refresh') in paths
    assert sends == 2
    assert len(paths) == (4 if refresh_status == 200 else 5)


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'id': 'id', 'status': 'rejected'}, {'id': '', 'status': 'waiting'}, {'id': 12, 'status': 'waiting'}])
async def test_sms_success_requires_valid_queued_acknowledgement(body):
    from services.eskiz_client import EskizClient
    from core.exceptions import OTPSendFailedException
    def handle(request):
        if request.url.path.endswith('login'):
            return httpx.Response(200, json={'data': {'token': 'token'}})
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = EskizClient(email='test', password='test', sender_id='4546', base_url='https://notify.eskiz.uz', timeout_seconds=5, http_client=http)
        with pytest.raises(OTPSendFailedException):
            await client.send_sms('+998901234567', '123456')
