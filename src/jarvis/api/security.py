"""Network security configuration (Phase 7).

Centralises the CORS, trusted-host, security-header and reverse-proxy
behaviour that the FastAPI app installs at startup. Everything here is pure
configuration built from settings — the middleware wiring lives in
``jarvis.api.main.build_security_stack``.

Rules enforced here (and validated by ``jarvis.config.deployment``):

* local profile binds loopback-only by default and installs **no** CORS
  middleware unless explicit origins are configured.
* single_host/production require explicit (non-wildcard) allowed origins and
  a trusted-host allowlist; wildcard origins are rejected by validation.
* Security headers are applied to every response (except HSTS, which is only
  advertised when ``JARVIS_FORCE_HTTPS=true``).
* Proxy headers are only honoured when ``JARVIS_BEHIND_REVERSE_PROXY=true``.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from jarvis.config.settings import Settings, settings

# Methods the API is designed to serve; kept explicit rather than wildcard.
_ALLOWED_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
# Standard security headers applied to every response.
_BASE_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def cors_origins(s: Settings | None = None) -> list[str]:
    """Allowed CORS origins; empty list = no cross-origin access."""
    if s is None:
        s = settings
    return s.allowed_origins_list


def cors_enabled(s: Settings | None = None) -> bool:
    """True when CORS middleware should be installed (explicit origins set)."""
    return bool(cors_origins(s))


def trusted_hosts(s: Settings | None = None) -> list[str]:
    """Host-header allowlist; empty = TrustedHostMiddleware is not installed."""
    if s is None:
        s = settings
    hosts = s.trusted_hosts_list
    if _is_test_env():
        # FastAPI's TestClient sends Host: testserver. Only when running the
        # pytest suite do we permit that host — production never sees it.
        hosts = [h for h in hosts if h != "testserver"] + ["testserver"]
    return hosts


def _is_test_env() -> bool:
    import os

    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("JARVIS_TEST_MODE"))


def security_headers(s: Settings | None = None) -> dict[str, str]:
    """The security headers to stamp on every response.

    HSTS is included only when ``JARVIS_FORCE_HTTPS=true`` so a local HTTP
    deployment never advertises an invalid HSTS policy.
    """
    if s is None:
        s = settings
    headers = dict(_BASE_SECURITY_HEADERS)
    if s.jarvis_force_https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def install_security_stack(app: FastAPI, s: Settings | None = None) -> None:
    """Install CORS, trusted-host, header and proxy-awareness middleware."""
    if s is None:
        s = settings

    if cors_enabled(s):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins(s),
            allow_credentials=True,
            allow_methods=_ALLOWED_METHODS,
            allow_headers=["*"],
        )

    hosts = trusted_hosts(s)
    if hosts:
        app.add_middleware(_TrustedHostMiddleware)

    app.add_middleware(_SecurityHeadersMiddleware)

    if s.jarvis_behind_reverse_proxy:
        app.add_middleware(_TrustedProxyMiddleware)


class _TrustedHostMiddleware:
    """Rejects requests whose Host header is not in the trusted allowlist.

    The allowlist is resolved on every request from ``settings`` so changes
    made after startup (e.g. the pytest suite) are honoured, and so the test
    client's ``testserver`` host only ever counts inside the test environment.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        host = None
        for k, v in scope.get("headers") or []:
            if k.decode("latin-1").lower() == "host":
                host = v.decode("latin-1")
                break
        if host:
            hostname = host.split(":")[0]
            allowed = trusted_hosts()
            if not any(_host_matches(hostname, pattern) for pattern in allowed):
                body = b"Bad Request"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode("latin-1")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        return host == pattern[2:] or host.endswith(pattern[1:])
    return host == pattern


class _SecurityHeadersMiddleware:
    """Starlette middleware that stamps security headers on every response."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.headers = security_headers()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                for name, value in self.headers.items():
                    message["headers"].append((name.lower().encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, _send)


class _TrustedProxyMiddleware:
    """Honour X-Forwarded-For only when the app sits behind a trusted proxy.

    Rewrites the request client so rate limiting keys on the real client IP
    rather than the proxy's. Only installed when
    ``JARVIS_BEHIND_REVERSE_PROXY=true``.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in (scope.get("headers") or [])}
            forwarded = headers.get("x-forwarded-for") or headers.get("x-real-ip")
            if forwarded:
                first = forwarded.split(",")[0].strip()
                if first:
                    scope["client"] = (first, scope.get("client", ("", 0))[1])
        await self.app(scope, receive, send)


def client_ip(request: Request) -> str:
    """Best-effort client IP (already proxy-adjusted by the middleware)."""
    return request.client.host if request.client else "unknown"


__all__ = [
    "client_ip",
    "cors_enabled",
    "cors_origins",
    "install_security_stack",
    "security_headers",
    "trusted_hosts",
]