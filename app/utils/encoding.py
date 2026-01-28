import time
import secrets
import string


class UrlTokenStore:

    def __init__(self, ttl_seconds=3600):
        self.ttl = ttl_seconds
        self.store = {} 

    def _generate_token(self, length=8):
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def cleanup(self):
        now = time.time()

        expired = [
            token for token, (_, exp) in self.store.items()
            if exp < now
        ]

        for token in expired:
            del self.store[token]
            
    def encode(self, url: str) -> str:
        token = self._generate_token()
        expires_at = time.time() + self.ttl

        self.store[token] = (url, expires_at)

        return token
    
    def decode(self, token: str) -> str | None:
        self.cleanup()  

        entry = self.store.get(token)

        if not entry:
            return None

        url, _ = entry
        return url


token_store = UrlTokenStore()
