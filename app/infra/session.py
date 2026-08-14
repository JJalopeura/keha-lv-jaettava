"""Small signed-cookie session adapter for the offline demo application."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any


ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]


class DemoSessionMiddleware:
    """Expose a signed ``scope['session']`` dictionary without extra packages."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        secret_key: str,
        *,
        session_cookie: str = "lv_session",
        same_site: str = "lax",
        https_only: bool = False,
    ) -> None:
        self.app = app
        self.secret = secret_key.encode("utf-8")
        self.session_cookie = session_cookie
        self.same_site = same_site
        self.https_only = https_only

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope["session"] = self._decode_cookie(self._request_cookie(scope))

        async def send_with_cookie(message: ASGIMessage) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if scope["session"]:
                    value = self._encode_cookie(scope["session"])
                    cookie = f"{self.session_cookie}={value}; Path=/; HttpOnly; SameSite={self.same_site}"
                else:
                    cookie = f"{self.session_cookie}=; Path=/; Max-Age=0; HttpOnly; SameSite={self.same_site}"
                if self.https_only:
                    cookie += "; Secure"
                headers.append((b"set-cookie", cookie.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _request_cookie(self, scope: dict[str, Any]) -> str:
        for name, value in scope.get("headers", []):
            if name.lower() != b"cookie":
                continue
            for item in value.decode("latin-1").split(";"):
                key, separator, cookie_value = item.strip().partition("=")
                if separator and key == self.session_cookie:
                    return cookie_value
        return ""

    def _encode_cookie(self, session: dict[str, Any]) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps(session, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).rstrip(b"=")
        signature = hmac.new(self.secret, payload, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        return f"{payload.decode('ascii')}.{encoded_signature.decode('ascii')}"

    def _decode_cookie(self, value: str) -> dict[str, Any]:
        if not value or "." not in value:
            return {}
        payload_text, signature_text = value.rsplit(".", 1)
        payload = payload_text.encode("ascii")
        expected = hmac.new(self.secret, payload, hashlib.sha256).digest()
        try:
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            if not hmac.compare_digest(signature, expected):
                return {}
            decoded = base64.urlsafe_b64decode(payload + b"=" * (-len(payload) % 4))
            result = json.loads(decoded.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return result if isinstance(result, dict) else {}
