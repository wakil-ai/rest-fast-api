"""Eskiz's published Postman contract; never log requests or raw errors."""
import httpx

from core.exceptions import OTPSendFailedException


class EskizClient:
    def __init__(self, *, email, password, sender_id, base_url, timeout_seconds, http_client=None):
        self._email = email
        self._password = password
        self._sender_id = sender_id
        self._base_url = base_url.rstrip('/')
        self._timeout = timeout_seconds
        self._http = http_client
        self._token = None

    async def _request(self, method, path, *, fields=None, authenticated=False):
        kwargs = {'timeout': self._timeout}
        if fields is not None:
            kwargs['files'] = {key: (None, value) for key, value in fields.items()}
        if authenticated:
            kwargs['headers'] = {'Authorization': f'Bearer {self._token}'}
        if self._http is not None:
            return await self._http.request(method, self._base_url + path, **kwargs)
        async with httpx.AsyncClient() as http:
            return await http.request(method, self._base_url + path, **kwargs)

    async def _login(self):
        response = await self._request('POST', '/api/auth/login', fields={'email': self._email, 'password': self._password})
        response.raise_for_status()
        self._token = response.json()['data']['token']

    async def send_sms(self, phone, code, locale='uz'):
        try:
            if not self._token:
                await self._login()
            message = {
                'uz': f'Wakil AI tasdiqlash kodi: {code}',
                'ru': f'Wakil AI: код подтверждения {code}',
                'en': f'Wakil AI verification code: {code}',
            }[locale]
            fields = {'mobile_phone': phone.lstrip('+'), 'message': message, 'from': self._sender_id}
            response = await self._request('POST', '/api/message/sms/send', fields=fields, authenticated=True)
            if response.status_code == 401:
                refresh = await self._request('PATCH', '/api/auth/refresh', authenticated=True)
                if refresh.status_code == 401:
                    await self._login()
                else:
                    refresh.raise_for_status()
                    self._token = refresh.json()['data']['token']
                response = await self._request('POST', '/api/message/sms/send', fields=fields, authenticated=True)
            response.raise_for_status()
            body = response.json()
            if body.get('status') != 'waiting' or not isinstance(body.get('id'), str) or not body['id']:
                raise OTPSendFailedException()
            return body['id']
        except Exception:
            raise OTPSendFailedException() from None
