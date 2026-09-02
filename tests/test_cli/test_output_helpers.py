from __future__ import annotations

import json
import sys

from agentos.cli.output import emit_error, print_json


def test_print_json_with_ascii(capfd):
    """Simple ASCII payload round-trips correctly."""
    print_json({"msg": "hello"})
    out, err = capfd.readouterr()
    payload = json.loads(out)
    assert payload["msg"] == "hello"
    assert err == ""


def test_print_json_with_non_ascii(capfd):
    """Non-ASCII UTF-8 characters survive the round trip."""
    print_json({"text": "héllo wörld 🔥", "value": object()})
    out, err = capfd.readouterr()
    payload = json.loads(out)
    assert payload["text"] == "héllo wörld 🔥"
    assert err == ""


def test_emit_error_json_uses_stderr(capfd):
    emit_error(
        "bad input",
        json_output=True,
        code="INVALID_REQUEST",
        details={"field": "x"},
    )

    out, err = capfd.readouterr()
    assert out == ""
    payload = json.loads(err)
    assert payload == {
        "error": {
            "message": "bad input",
            "code": "INVALID_REQUEST",
            "details": {"field": "x"},
        }
    }


def test_emit_error_with_non_ascii(capfd):
    """Non-ASCII characters in error payloads also survive."""
    emit_error(
        "não é possível — é o fim 🔥",
        json_output=True,
        code="INVALID_REQUEST",
    )
    out, err = capfd.readouterr()
    assert out == ""
    payload = json.loads(err)
    assert payload["error"]["message"] == "não é possível — é o fim 🔥"


class _FailingBinaryBuffer:
    """A mock binary buffer that rejects non-ASCII bytes."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        for b in data:
            if b > 127:
                raise UnicodeEncodeError(
                    "utf-8",
                    "simulated",
                    0,
                    1,
                    "simulated terminal that cannot handle non-ASCII",
                )
            self._buf.append(b)

    def flush(self) -> None:
        pass

    def getvalue(self) -> bytes:
        return bytes(self._buf)


class _RestrictedStdout:
    """A fake sys.stdout whose .buffer raises on non-ASCII bytes."""

    def __init__(self) -> None:
        self.buffer = _FailingBinaryBuffer()
        self.encoding = "utf-8"

    def flush(self) -> None:
        self.buffer.flush()


def test_print_json_fallback_ensure_ascii():
    """When even the buffer cannot encode, fall back to ensure_ascii=True."""
    original_stdout = sys.stdout
    try:
        sys.stdout = _RestrictedStdout()
        print_json({"text": "héllo café 100€"})
        sys.stdout.flush()
    finally:
        sys.stdout = original_stdout

    payload = json.loads(
        sys.stdout.buffer.getvalue().decode("utf-8")  # type: ignore[union-attr]
    )
    assert payload == {"text": "héllo café 100€"}
    raw = sys.stdout.buffer.getvalue()  # type: ignore[union-attr]
    assert all(b < 128 for b in raw), "expected only ASCII bytes in fallback output"
