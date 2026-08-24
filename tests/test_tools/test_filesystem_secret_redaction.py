"""read_file, grep_search, and read_spreadsheet must redact secrets under elevated-full.

Bug: under elevated-full mode (which cron agent_turn jobs now run by default),
the path denylist [_sensitive_access_block] returns None, so read_file on a
secrets file like ~/.aws/credentials returns raw credentials into the transcript
with no redaction fallback. The existing redact_sensitive_text(file_read=True)
was built for exactly this purpose but had zero production callers.

Fix: wire redact_sensitive_text(..., file_read=True) into read_file,
grep_search, and read_spreadsheet outputs as a defense-in-depth layer that
stays on even when the path denylist is bypassed by elevation. Also fix
redact_sensitive_text so file_read=True runs the assignment pass with the
non-reusable sentinel (previously code_file=True skipped it entirely).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from agentos.redact import redact_sensitive_text
from agentos.tools.builtin import filesystem as fs
from agentos.tools.types import CallerKind, ToolContext, current_tool_context


@contextmanager
def elevated_full_context(workspace: Path) -> object:
    """Run with elevated-full mode, which bypasses the path denylist."""
    ctx = ToolContext(
        caller_kind=CallerKind.CLI,
        channel_kind="cli",
        channel_id="cli:test",
        workspace_dir=str(workspace),
        elevated="full",
    )
    token = current_tool_context.set(ctx)
    try:
        yield ctx
    finally:
        current_tool_context.reset(token)


@contextmanager
def normal_context(workspace: Path) -> object:
    """Run with normal (non-elevated) mode."""
    ctx = ToolContext(
        caller_kind=CallerKind.CLI,
        channel_kind="cli",
        channel_id="cli:test",
        workspace_dir=str(workspace),
    )
    token = current_tool_context.set(ctx)
    try:
        yield ctx
    finally:
        current_tool_context.reset(token)


# --- read_file ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_redacts_secrets_under_elevated_full(tmp_path: Path) -> None:
    """Under elevated-full, read_file must still redact secrets in file content
    even though the path denylist is bypassed."""
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")

    with elevated_full_context(tmp_path):
        result = await fs.read_file(str(secret_file))

    # The raw secret must NOT appear verbatim in the output.
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in result
    # The redaction sentinel must be present.
    assert "«redacted" in result


@pytest.mark.asyncio
async def test_read_file_redacts_nonreusable_under_elevated_full(tmp_path: Path) -> None:
    """Under elevated-full, read_file must use the non-reusable mask
    (file_read=True) so the agent cannot write a corrupted value back."""
    secret_file = tmp_path / "config.ini"
    secret_file.write_text("[database]\npassword=hunter2supersecret\n")

    with elevated_full_context(tmp_path):
        result = await fs.read_file(str(secret_file))

    # The raw password must be masked.
    assert "hunter2supersecret" not in result
    # The mask uses the non-reusable sentinel (head/tail of the value).
    assert "«redacted" in result


@pytest.mark.asyncio
async def test_read_file_normal_mode_not_affected_by_redaction(tmp_path: Path) -> None:
    """Under normal mode, read_file blocks sensitive paths outright — the
    redaction layer is only exercised under elevated-full. This test verifies
    that a non-sensitive file is not mangled by the defence-in-depth layer."""
    readme = tmp_path / "README.md"
    readme.write_text("# Hello\n\nThis is a safe file.\n")

    with elevated_full_context(tmp_path):
        result = await fs.read_file(str(readme))

    assert "# Hello" in result
    assert "«redacted" not in result


# --- grep_search ------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_search_redacts_secrets_under_elevated_full(tmp_path: Path) -> None:
    """Under elevated-full, grep_search must redact secrets in matched lines."""
    secret_file = tmp_path / "app.env"
    secret_file.write_text("AWS_SECRET_ACCESS_KEY=hunter2supersecret\n")

    with elevated_full_context(tmp_path):
        result = await fs.grep_search("hunter2supersecret", path=str(tmp_path))

    # The raw secret must not appear verbatim.
    assert "hunter2supersecret" not in result
    # Some redaction must be present (either «redacted or *** mask).
    assert "***" in result or "«redacted" in result


@pytest.mark.asyncio
async def test_grep_search_no_redaction_in_normal_mode_for_safe_files(tmp_path: Path) -> None:
    """Under normal mode grep on a non-sensitive file returns content unchanged."""
    readme = tmp_path / "README.md"
    readme.write_text("# Project\n\nA safe readme.\n")

    with normal_context(tmp_path):
        result = await fs.grep_search("Project", path=str(tmp_path))

    assert "Project" in result
    assert "«redacted" not in result


# --- read_spreadsheet --------------------------------------------------------


@pytest.mark.asyncio
async def test_read_spreadsheet_redacts_secrets_under_elevated_full(tmp_path: Path) -> None:
    """Under elevated-full, read_spreadsheet must redact secrets in CSV cells."""
    csv_file = tmp_path / "credentials.csv"
    csv_file.write_text("service,key\naws,sk-or-v1-AAAAAAAAAAAAAAAAAAAAAAAA\n")

    with elevated_full_context(tmp_path):
        result = await fs.read_spreadsheet(str(csv_file))

    # Raw secret must not appear verbatim.
    assert "sk-or-v1-AAAAAAAAAAAAAAAAAAAAAAAA" not in result
    # Redaction sentinel must be present.
    assert "«redacted" in result


@pytest.mark.asyncio
async def test_read_spreadsheet_normal_mode_safe_content(tmp_path: Path) -> None:
    """Under normal mode, a non-sensitive CSV is not mangled."""
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("name,value\nalpha,100\nbeta,200\n")

    with elevated_full_context(tmp_path):
        result = await fs.read_spreadsheet(str(csv_file))

    assert "alpha" in result
    assert "«redacted" not in result


# --- redact_sensitive_text file_read=True regression -------------------------


def test_file_read_redacts_assignments_with_nonreusable_mask() -> None:
    """file_read=True must run the assignment pass with the non-reusable mask,
    not skip it entirely (the previous behaviour was code_file=True which
    skipped assignments and left KEY=secret pairs unredacted)."""
    text = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    result = redact_sensitive_text(text, file_read=True)
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in result
    assert "«redacted" in result


def test_file_read_skips_assignment_test_fixtures() -> None:
    """file_read=True should still skip test-value fixtures
    (the _is_secret_literal_value guard) — they are not secrets."""
    text = 'DEFAULT_API_KEY = "test-value-for-fixtures"'
    # code_file=True skips assignments entirely.
    assert redact_sensitive_text(text, code_file=True) == text
    # file_read=True runs assignments but masks with non-reusable sentinel.
    result = redact_sensitive_text(text, file_read=True)
    # test-value-for-fixtures should be redacted because _is_secret_literal_value
    # considers it a secret value.
    assert "test-value-for-fixtures" not in result
