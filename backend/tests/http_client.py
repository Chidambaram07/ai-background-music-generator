from uuid import uuid4

from fastapi.testclient import TestClient


class IdempotentTestClient(TestClient):
    auto_idempotency = True

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        if method.upper() == "POST" and self.auto_idempotency and not any(name.lower() == "idempotency-key" for name in headers):
            headers["Idempotency-Key"] = str(uuid4())
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)