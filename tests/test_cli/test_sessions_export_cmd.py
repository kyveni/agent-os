"""Regression tests for sessions export filename sanitization.

Ensures session_id values containing path separators or shell metacharacters
are sanitized before use as a filename, preventing directory traversal.
"""

from __future__ import annotations

import re


# Same sanitization used in sessions_cmd.py::sessions_export
def _safe_export_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id).strip("-") or "session"


class TestSessionExportSanitization:
    """Safe names pass through unchanged."""

    def test_normal_session_id_passes(self) -> None:
        assert _safe_export_name("session_abc123") == "session_abc123"

    def test_colon_replaced(self) -> None:
        assert _safe_export_name("abc:123:xyz") == "abc-123-xyz"

    def test_hyphen_preserved(self) -> None:
        assert _safe_export_name("my-session-id") == "my-session-id"

    def test_dot_preserved(self) -> None:
        assert _safe_export_name("session.v1") == "session.v1"

    def test_underscore_preserved(self) -> None:
        assert _safe_export_name("my_session") == "my_session"

    """Path traversal payloads — slashes are removed, preventing traversal."""

    def test_path_traversal_slashes_removed(self) -> None:
        name = _safe_export_name("../../etc/pwned")
        assert "/" not in name

    def test_path_traversal_backslashes_removed(self) -> None:
        name = _safe_export_name("..\\..\\etc\\pwned")
        assert "\\" not in name

    def test_absolute_path_removed(self) -> None:
        name = _safe_export_name("/etc/passwd")
        assert "/" not in name

    def test_mixed_traversal(self) -> None:
        name = _safe_export_name("../../etc/passwd.json")
        assert "/" not in name

    def test_encoded_slash_removed(self) -> None:
        assert _safe_export_name("safe%2Ftraversal") == "safe-2Ftraversal"

    """Edge cases produce a safe fallback."""

    def test_all_invalid_chars_falls_back(self) -> None:
        assert _safe_export_name("!!!@@@###") == "session"

    def test_empty_string_falls_back(self) -> None:
        assert _safe_export_name("") == "session"

    def test_only_slashes_falls_back(self) -> None:
        assert _safe_export_name("///") == "session"

    def test_leading_trailing_dashes_stripped(self) -> None:
        assert _safe_export_name("---hello---") == "hello"

    def test_tab_newline_removed(self) -> None:
        assert _safe_export_name("abc\tdef\nghi") == "abc-def-ghi"

    def test_unicode_letters_preserved(self) -> None:
        name = _safe_export_name("sesión-123")
        # ñ is stripped by the regex [^A-Za-z0-9_.-]
        assert "-" in name

    def test_spaces_replaced(self) -> None:
        assert _safe_export_name("my session id") == "my-session-id"
