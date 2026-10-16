from __future__ import annotations

import hmac
import ipaddress
import math
import secrets
import time
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

SESSION_COOKIE = "teachereval_local_session"
TOKEN_HEADER = "x-teachereval-csrf"
SESSION_PATH = "/api/v1/local/session"

class LocalBrowserSecurityMiddleware:
    def __init__(self, app, *, ttl_seconds=3600, max_sessions=64):
        self.app = app
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.sessions = {}

    @staticmethod
    def _origin(scope, headers):
        try:
            client = scope.get("client")
            if not client or not ipaddress.ip_address(client[0]).is_loopback:
                return None
            host = headers.get("host", "")
            parsed = urlsplit("http://" + host)
            if (parsed.hostname not in {"127.0.0.1", "localhost"}
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment):
                return None
            server = scope.get("server")
            port = parsed.port or 80
            if server and port != server[1]:
                return None
            return "http://" + host.lower()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _cookies(headers):
        from http.cookies import SimpleCookie
        try:
            values = SimpleCookie()
            values.load(headers.get("cookie", ""))
            return values
        except Exception:
            return {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin1").lower(): v.decode("latin1")
                   for k, v in scope.get("headers", [])}
        origin = self._origin(scope, headers)
        path, method = scope.get("path", ""), scope.get("method", "GET")
        protected = path.startswith("/api/") or path == "/health" or method not in {"GET", "HEAD"}

        async def secure_send(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend([
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"frame-ancestors 'none'"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ])
                if protected:
                    response_headers.append((b"cache-control", b"no-store"))
                message = dict(message, headers=response_headers)
            await send(message)

        async def reject(code):
            response = JSONResponse({"error": code}, status_code=403)
            await response(scope, receive, secure_send)

        if origin is None:
            return await reject("LOCAL_HOST_REQUIRED")
        request_origin = headers.get("origin")
        if protected and (headers.get("sec-fetch-site") not in {None, "same-origin", "none"}
                          or (request_origin is not None and request_origin.lower() != origin)):
            return await reject("SAME_ORIGIN_REQUIRED")
        if method not in {"GET", "HEAD"} and request_origin != origin:
            return await reject("SAME_ORIGIN_REQUIRED")

        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
        if path == SESSION_PATH:
            if method != "GET":
                return await reject("SESSION_GET_REQUIRED")
            cookie = self._cookies(headers).get(SESSION_COOKIE)
            session = self.sessions.get(cookie.value if cookie else "")
            if session and session[2] == origin:

                response = JSONResponse({"csrf_token": session[0],
                                         "expires_in": math.ceil(session[1] - now)})
                return await response(scope, receive, secure_send)
            if len(self.sessions) >= self.max_sessions:
                return await reject("LOCAL_SESSION_LIMIT")
            session_id, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.sessions[session_id] = (token, now + self.ttl_seconds, origin)
            response = JSONResponse({"csrf_token": token, "expires_in": self.ttl_seconds})
            response.set_cookie(SESSION_COOKIE, session_id, httponly=True,
                                samesite="strict", path="/", max_age=self.ttl_seconds)
            return await response(scope, receive, secure_send)
        if protected:
            cookie = self._cookies(headers).get(SESSION_COOKIE)
            session = self.sessions.get(cookie.value if cookie else "")
            supplied = headers.get(TOKEN_HEADER, "")
            if (not session or session[2] != origin or not supplied
                    or not hmac.compare_digest(session[0], supplied)):
                return await reject("LOCAL_SESSION_REQUIRED")
        return await self.app(scope, receive, secure_send)
