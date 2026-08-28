"""Binary connection admission for gateway clients."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agentos.gateway.access import ConnectionSurface, is_loopback_address, is_loopback_bind

if TYPE_CHECKING:
    from agentos.gateway.config import GatewayConfig

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Server-computed connection admission.

    The context intentionally carries no role, scope, admin, or owner flag.
    ``admitted`` is the entire human authorization state. ``surface`` limits
    which protocol contract the connection may use.
    """

    surface: ConnectionSurface
    admitted: bool
    credential_verified: bool


def denied_access(surface: ConnectionSurface = ConnectionSurface.CONTROL) -> AccessContext:
    """Build a fail-closed context for a rejected connection."""

    return AccessContext(surface=surface, admitted=False, credential_verified=False)


def token_matches(provided: object, configured: str | None) -> bool:
    """Constant-time check of a caller-supplied token against the secret.

    Fails closed on a missing/empty configured token and on any non-string or
    missing provided value. Comparison runs on UTF-8 bytes via
    ``hmac.compare_digest`` so response time does not reveal the secret's
    matching prefix (#498).
    """

    if not configured or not isinstance(provided, str) or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))


def _surface(value: str | ConnectionSurface | None) -> ConnectionSurface:
    try:
        return ConnectionSurface(value or ConnectionSurface.CONTROL)
    except ValueError as exc:
        raise ValueError(f"Invalid client kind: {value!r}") from exc


def resolve_auth(
    config: GatewayConfig,
    auth_params: dict,
    client_kind: str | ConnectionSurface = ConnectionSurface.CONTROL,
    *,
    peer_ip: str | None = None,
) -> AccessContext | None:
    """Authenticate one connection and return its fixed protocol surface.

    ``mode=none`` is accepted only when both the listener and peer are
    loopback. Token mode is all-or-nothing: a valid token admits the complete
    selected surface. Fine-grained human scopes are deliberately unsupported.
    """

    try:
        surface = _surface(client_kind)
    except ValueError as exc:
        log.warning("auth.failed", mode=config.auth.mode, error=str(exc))
        return None
    if surface not in {ConnectionSurface.CONTROL, ConnectionSurface.NODE}:
        log.warning("auth.failed", mode=config.auth.mode, error="unsupported client surface")
        return None

    if config.auth.mode == "token":
        provided = (auth_params or {}).get("token")
        if not token_matches(provided, config.auth.token):
            log.warning("auth.failed", mode=config.auth.mode, error="invalid token")
            return None
        return AccessContext(
            surface=surface,
            admitted=True,
            credential_verified=True,
        )

    if config.auth.mode == "none":
        local = is_loopback_bind(config.host) and is_loopback_address(peer_ip)
        if not local:
            log.warning(
                "auth.failed",
                mode=config.auth.mode,
                error="no-auth connections require a loopback listener and peer",
            )
            return None
        return AccessContext(
            surface=surface,
            admitted=True,
            credential_verified=False,
        )

    log.warning("auth.unsupported_mode", mode=config.auth.mode)
    return None


__all__ = ["AccessContext", "denied_access", "resolve_auth", "token_matches"]
