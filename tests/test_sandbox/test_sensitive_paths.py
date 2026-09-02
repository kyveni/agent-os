from __future__ import annotations

from pathlib import Path

from agentos.sandbox.sensitive_paths import (
    is_sensitive_path,
    sensitive_path_in_text,
    sensitive_path_marker,
    sensitive_target_in_command,
)


def test_sensitive_path_matches_nested_home_prefixes_with_native_separators() -> None:
    assert is_sensitive_path(str(Path.home() / ".ssh" / "id_rsa")) == "~/.ssh"
    assert is_sensitive_path(str(Path.home() / ".aws" / "credentials")) == "~/.aws"


def test_sensitive_path_in_text_matches_native_separator_paths() -> None:
    key_path = Path.home() / ".ssh" / "id_rsa"

    assert sensitive_path_in_text(f"type {key_path}") == "~/.ssh"


def test_active_workspace_under_root_is_not_blocked_by_root_prefix() -> None:
    workspace = Path("/root/.agentos/workspace")

    assert (
        sensitive_path_marker(
            str(workspace / "notes" / "plan.md"),
            workspace=workspace,
        )
        is None
    )
    assert (
        sensitive_path_in_text(
            f"cat {workspace / 'notes' / 'plan.md'}",
            workspace=workspace,
        )
        is None
    )


def test_active_workspace_exception_keeps_leaf_secret_blocks() -> None:
    workspace = Path("/root/.agentos/workspace")

    assert sensitive_path_marker(str(workspace / ".env"), workspace=workspace) in {
        "/.env",
        "/.env*",
    }
    assert sensitive_path_marker(str(workspace / "id_rsa"), workspace=workspace) == "/id_rsa"
    assert (
        sensitive_path_in_text(
            f"cat {workspace / '.env.local'}",
            workspace=workspace,
        )
        in {"/.env.local", "/.env*"}
    )


def test_sensitive_command_targets_honor_active_workspace_exception() -> None:
    workspace = Path("/root/.agentos/workspace")

    assert (
        sensitive_target_in_command(
            f"rm {workspace / 'scratch.txt'}",
            workspace=workspace,
        )
        is None
    )
    assert (
        sensitive_target_in_command(
            f"rm {workspace / '.env'}",
            workspace=workspace,
        )
        in {"/.env", "/.env*"}
    )


def test_windows_rooted_workspace_targets_keep_leaf_secret_blocks() -> None:
    workspace = Path("/root/.agentos/workspace")

    assert (
        sensitive_target_in_command(
            r"rm \root\.agentos\workspace\scratch.txt",
            workspace=workspace,
        )
        is None
    )
    assert (
        sensitive_target_in_command(
            r"rm \root\.agentos\workspace\.env",
            workspace=workspace,
        )
        in {"/.env", "/.env*"}
    )


def test_posix_sensitive_paths_stay_blocked_on_windows_runners() -> None:
    workspace = Path("/root/.agentos/workspace")

    assert sensitive_path_in_text("cat /dev/sda 2>/dev/null") == "/dev"
    assert (
        sensitive_path_in_text("cat /root/.ssh/id_rsa", workspace=workspace)
        == "~/.ssh"
    )


def test_every_rm_in_a_compound_command_is_checked() -> None:
    """Issue #676: a benign leading ``rm`` must not shadow a later one.

    Each shell separator ends one ``rm`` invocation, so ``rm /tmp/ok; rm -rf
    /root`` yields both targets and the sensitive one wins.
    """
    workspace = Path("/workspace")

    for separator in (";", "&&", "||", "|", "&", "\n"):
        command = f"rm /tmp/ok {separator} rm -rf /root"
        assert sensitive_target_in_command(command, workspace=workspace) == "/root", command

    assert (
        sensitive_target_in_command(
            "rm /tmp/ok; shutil.rmtree('/etc/ssl')",
            workspace=workspace,
        )
        == "/etc"
    )


def test_sensitive_reads_in_a_later_segment_are_blocked_at_the_tool_boundary() -> None:
    """Issue #676: the delete-intent scan only sees ``rm`` targets, so a
    non-destructive second segment (``cat /root/.bash_history``) is caught by
    the text scan ``exec_command`` runs alongside it, not by this one."""
    workspace = Path("/workspace")

    assert sensitive_target_in_command("rm /tmp/ok; ls /root", workspace=workspace) is None
    assert sensitive_path_in_text("rm /tmp/ok; ls /root", workspace=workspace) == "/root"
    assert (
        sensitive_path_in_text("rm /tmp/ok; cat /root/.bash_history", workspace=workspace)
        == "/root"
    )


def test_sensitive_path_blocks_root_filesystem() -> None:
    """Issue #563: ``rm -rf /``, ``rm -rf /*``, and ``rm -rf /.`` must
    be blocked by the hard block (sensitive_target_in_command) because
    ``/`` is not covered by _SENSITIVE_PREFIXES like ``/etc`` or ``/root``."""
    workspace = Path("/workspace")

    for cmd in [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf /.",
        "rm -rf / .",
    ]:
        result = sensitive_target_in_command(cmd, workspace=workspace)
        assert result == "/", f"Expected '/' marker for {cmd!r}, got {result!r}"


def test_sensitive_path_is_path_root() -> None:
    """Issue #563: ``is_sensitive_path`` must return ``'/'`` for root
    filesystem references."""
    for path in ("/", "/*", "/."):
        assert (
            is_sensitive_path(path) == "/"
        ), f"Expected '/' for {path!r}, got {is_sensitive_path(path)!r}"
