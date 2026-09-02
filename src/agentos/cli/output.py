"""Small shared output helpers for scriptable CLI commands."""

from __future__ import annotations

import json
import sys
from typing import Any

import typer


def print_json(payload: Any) -> None:
    """Print JSON payload to stdout using the AgentOS CLI contract.

    Writes UTF-8 bytes directly to stdout.buffer to avoid
    UnicodeEncodeError on terminals with non-UTF-8 encoding (e.g.
    Windows cp1252/cp437). Falls back to ensure_ascii=True on error.
    """

    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except UnicodeEncodeError:
        text = json.dumps(payload, ensure_ascii=True, default=str)
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def error_payload(
    message: str,
    *,
    code: str | None = None,
    details: Any | None = None,
) -> dict[str, Any]:
    """Build the small AgentOS-owned JSON error envelope."""

    error: dict[str, Any] = {"message": message}
    if code:
        error["code"] = code
    if details is not None:
        error["details"] = details
    return {"error": error}


def emit_error(
    message: str,
    *,
    json_output: bool = False,
    code: str | None = None,
    details: Any | None = None,
) -> None:
    """Emit an error to stderr without polluting JSON stdout."""

    if json_output:
        try:
            text = json.dumps(
                error_payload(message, code=code, details=details),
                ensure_ascii=False,
                default=str,
            )
            sys.stderr.buffer.write((text + "\n").encode("utf-8"))
            sys.stderr.buffer.flush()
        except UnicodeEncodeError:
            text = json.dumps(
                error_payload(message, code=code, details=details),
                ensure_ascii=True,
                default=str,
            )
            sys.stderr.buffer.write((text + "\n").encode("utf-8", errors="replace"))
            sys.stderr.buffer.flush()
    else:
        typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
