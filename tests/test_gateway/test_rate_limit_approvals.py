"""Regression tests for GET /api/approvals rate limiting (#569).

The approval-polling endpoint was previously exempt from per-IP rate limiting,
allowing an attacker to enumerate pending tool calls (info disclosure) and
stall the pipeline behind SQLite locks (DoS).

Fix: remove the exemption — /api/approvals counts against the same per-IP
bucket as the rest of /api/*.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from agentos.gateway.config import GatewayConfig
from agentos.gateway.middleware import RateLimitMiddleware


def test_approval_polling_is_rate_limited() -> None:
    """GET /api/approvals now counts against the per-IP rate limit.

    With max_requests=1, the second call in the same window is rejected.
    """
    app = Starlette()

    async def approvals(_request):
        return JSONResponse({"approvals": []})

    async def sessions(_request):
        return JSONResponse({"ok": True})

    app.add_route("/api/approvals", approvals, methods=["GET"])
    app.add_route("/api/sessions", sessions, methods=["GET"])
    config = GatewayConfig()
    config.rate_limit.enabled = True
    config.rate_limit.max_requests = 1
    config.rate_limit.window_seconds = 60
    app.add_middleware(RateLimitMiddleware, config=config)

    with TestClient(app) as client:
        # First request — passes
        assert client.get("/api/approvals").status_code == 200
        # Second request to /api/approvals — now rate-limited!
        assert client.get("/api/approvals").status_code == 429
        # Other API endpoint also rate-limited
        assert client.get("/api/sessions").status_code == 429


def test_approval_polling_reasonable_poll_rate_passes() -> None:
    """Normal UI polling (1 req/s) within a sane bucket passes."""
    app = Starlette()

    async def approvals(_request):
        return JSONResponse({"approvals": []})

    app.add_route("/api/approvals", approvals, methods=["GET"])
    config = GatewayConfig()
    config.rate_limit.enabled = True
    config.rate_limit.max_requests = 60
    config.rate_limit.window_seconds = 60
    app.add_middleware(RateLimitMiddleware, config=config)

    with TestClient(app) as client:
        # 3 quick polls — well within the 60 req/min limit
        for _ in range(3):
            assert client.get("/api/approvals").status_code == 200


def test_approval_polling_abuse_blocked() -> None:
    """Rapid polling (100 in <1s) hits the rate limit."""
    app = Starlette()

    async def approvals(_request):
        return JSONResponse({"approvals": []})

    app.add_route("/api/approvals", approvals, methods=["GET"])
    config = GatewayConfig()
    config.rate_limit.enabled = True
    config.rate_limit.max_requests = 10
    config.rate_limit.window_seconds = 60
    app.add_middleware(RateLimitMiddleware, config=config)

    with TestClient(app) as client:
        ok = 0
        denied = 0
        for _ in range(100):
            resp = client.get("/api/approvals")
            if resp.status_code == 200:
                ok += 1
            elif resp.status_code == 429:
                denied += 1
        # At most 10 should pass, the rest are denied
        assert ok <= 10
        assert denied >= 90
