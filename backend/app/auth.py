"""Optional single-workspace operator login for a shared demonstration."""
import hashlib
import hmac
import os
import secrets
import threading
import time
from fastapi import Request
from fastapi.responses import JSONResponse

COOKIE = "freshroute_session"


def configure_auth(app):
    password = os.environ.get("FRESHROUTE_OPERATOR_PASSWORD", "")
    if password and len(password) < 12:
        raise ValueError("FRESHROUTE_OPERATOR_PASSWORD must contain at least 12 characters.")
    enabled = bool(password)
    salt = secrets.token_bytes(32)
    expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000) if enabled else b""
    signing_key = secrets.token_bytes(32)
    attempts = {}
    sessions = {}
    attempt_lock = threading.Lock()
    ttl = 8 * 60 * 60

    def authenticated(request):
        if not enabled:
            return True
        token = request.cookies.get(COOKIE, "")
        try:
            expiry, nonce, signature = token.split(".")
            message = f"{expiry}.{nonce}"
            expected_sig = hmac.new(signing_key, message.encode(), hashlib.sha256).hexdigest()
            with attempt_lock:
                active = sessions.get(nonce) == int(expiry)
            return hmac.compare_digest(signature, expected_sig) and active and time.time() < int(expiry) <= time.time() + ttl
        except (ValueError, TypeError):
            return False

    def error(code, message, status):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})

    class WorkspaceMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await self.app(scope, receive, send)
            request = Request(scope, receive=receive)
            path = request.url.path
            public = path in ("/api/v1/auth/status", "/api/v1/auth/login", "/api/v1/auth/logout")
            protected = path.startswith("/api/") or path in ("/docs", "/redoc", "/openapi.json")
            if protected:
                origin = request.headers.get("origin")
                if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
                    allowed = set(os.environ.get("FRESHROUTE_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))
                    allowed.add(f"{request.url.scheme}://{request.url.netloc}")
                    if origin not in allowed:
                        return await error("ORIGIN_REJECTED", "This request origin is not allowed.", 403)(scope, receive, send)
                if enabled and not public and request.method != "OPTIONS" and not authenticated(request):
                    return await error("AUTHENTICATION_REQUIRED", "Sign in to access this workspace.", 401)(scope, receive, send)

            async def secure_send(message):
                if message['type'] == 'http.response.start':
                    message['headers'] = list(message.get('headers', [])) + [
                        (b'x-content-type-options', b'nosniff'),
                        (b'referrer-policy', b'same-origin'),
                        (b'x-frame-options', b'DENY'),
                    ]
                    if protected:
                        message['headers'].append((b'cache-control', b'no-store'))
                await send(message)
            await self.app(scope, receive, secure_send)

    app.add_middleware(WorkspaceMiddleware)

    @app.get("/api/v1/auth/status")
    def status(request: Request):
        return {"enabled": enabled, "authenticated": authenticated(request)}

    @app.post("/api/v1/auth/login")
    async def login(request: Request):
        if not enabled:
            return {"authenticated": True}
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with attempt_lock:
            # Bounded retention prevents unbounded growth for changing clients.
            for address in list(attempts):
                if not attempts[address] or now - attempts[address][-1] > 60:
                    del attempts[address]
            recent = [t for t in attempts.get(client, []) if now - t < 60]
            if len(recent) >= 5:
                return error("LOGIN_RATE_LIMIT", "Too many attempts. Retry in one minute.", 429)
            attempts[client] = recent + [now]
        try:
            body = await request.json()
            supplied = body.get("password", "") if isinstance(body, dict) else ""
            if not isinstance(supplied, str) or len(supplied) > 1024:
                supplied = ""
        except (ValueError, UnicodeError):
            supplied = ""
        digest = hashlib.pbkdf2_hmac("sha256", supplied.encode(), salt, 260000)
        if not hmac.compare_digest(digest, expected):
            return error("INVALID_CREDENTIALS", "Incorrect workspace password.", 401)
        nonce = secrets.token_hex(16)
        expires = int(time.time()) + ttl
        with attempt_lock:
            for existing in list(sessions):
                if sessions[existing] <= time.time():
                    del sessions[existing]
            if len(sessions) >= 1000:
                return error("SESSION_CAPACITY", "Too many active sessions. Try later.", 429)
            sessions[nonce] = expires
        message = f"{expires}.{nonce}"
        signature = hmac.new(signing_key, message.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"authenticated": True})
        response.set_cookie(COOKIE, f"{message}.{signature}", max_age=ttl, httponly=True,
                            secure=request.url.scheme == "https" or os.environ.get("FRESHROUTE_SECURE_COOKIES") == "1", samesite="strict", path="/")
        return response

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        if enabled and authenticated(request):
            with attempt_lock:
                sessions.pop(request.cookies.get(COOKIE, "").split(".")[1], None)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/")
        return response
