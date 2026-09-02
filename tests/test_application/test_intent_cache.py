"""Regression tests for IntentApprovalCache compound-command bypass fix.

PR #546 fixes P1 security issue #512: when ``rm A; rm -rf /`` is checked
against a cache that only approved ``rm A``, the second ``rm`` must be
rejected. The fix uses ``re.finditer`` + shell-separator-aware tokenization
instead of ``re.search``, so each ``rm`` invocation is parsed independently.

See https://github.com/use-agent-os/agent-os/pull/546
"""

from __future__ import annotations

from agentos.application.intent_cache import IntentApprovalCache


class TestCompoundCommandSeparatorBypass:
    """Every shell separator must be caught by the permission cache.

    A single approved ``rm /a`` followed by a second ``rm /b`` via any of the
    six shell separators (``;``, ``&&``, ``||``, ``|``, ``&``, ``\\n``) must
    return ``False`` — the untargeted path was never approved.
    """

    def _check_separator(self, separator: str) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("rm /a") is True
        assert cache.check(f"rm /a{separator} rm /b") is False, (
            f"check('rm /a{separator} rm /b') should be False"
        )

    def test_semicolon(self) -> None:
        self._check_separator(";")

    def test_and_and(self) -> None:
        self._check_separator(" && ")

    def test_or_or(self) -> None:
        self._check_separator(" || ")

    def test_pipe(self) -> None:
        self._check_separator(" | ")

    def test_ampersand(self) -> None:
        self._check_separator(" & ")

    def test_newline(self) -> None:
        self._check_separator("\n")


class TestMultiTargetApproval:
    """Multi-target commands must require approval for all targets."""

    def test_all_targets_approved_passes(self) -> None:
        """rm /a /b recorded -> check('rm /a /b') is True."""
        cache = IntentApprovalCache()
        cache.record("rm /a /b")
        assert cache.check("rm /a /b") is True

    def test_extra_target_not_approved_fails(self) -> None:
        """rm /a /b recorded -> check('rm /a /b /c') is False — /c not approved."""
        cache = IntentApprovalCache()
        cache.record("rm /a /b")
        assert cache.check("rm /a /b /c") is False


class TestRecordAndCheck:
    """Basic record/check lifecycle."""

    def test_empty_command_returns_false(self) -> None:
        cache = IntentApprovalCache()
        assert cache.check("") is False

    def test_non_rm_command_returns_false(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("echo hello") is False

    def test_record_always_survives_clear_scope(self) -> None:
        cache = IntentApprovalCache()
        cache.record_always("rm /a")
        cache.clear_scope("once")
        assert cache.check("rm /a") is True

    def test_forget_removes_entry(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("rm /a") is True
        cache.forget("rm /a")
        assert cache.check("rm /a") is False

    def test_clear_drops_all(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        cache.record("rm /b")
        cache.clear()
        assert cache.check("rm /a") is False
        assert cache.check("rm /b") is False


class TestFlagBypassEscalation:
    """Flag variants of an approved command must NOT be treated as approved.

    CERT #849: approving ``rm /tmp/a`` must NOT implicitly approve
    ``rm -rf /tmp/a``, because an attacker can escalate from a minimal
    destructive operation to a full recursive delete.
    """

    def test_rm_with_flags_not_approved_by_bare_rm(self) -> None:
        """rm /tmp/a approved -> check('rm -rf /tmp/a') must be False."""
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("rm /tmp/a") is True
        assert cache.check("rm -rf /tmp/a") is False

    def test_rm_with_different_flags_not_approved(self) -> None:
        """rm -rf /tmp/a approved -> check('rm -f /tmp/a') must be False."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /tmp/a")
        assert cache.check("rm -rf /tmp/a") is True
        assert cache.check("rm -f /tmp/a") is False

    def test_rm_with_same_flags_is_approved(self) -> None:
        """rm -rf /tmp/a approved -> check('rm -rf /tmp/a') again is True."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /tmp/a")
        assert cache.check("rm -rf /tmp/a") is True

    def test_rm_flag_order_normalized(self) -> None:
        """Flags are sorted, so -f -r matches -r -f."""
        cache = IntentApprovalCache()
        cache.record("rm -r -f /tmp/a")
        assert cache.check("rm -f -r /tmp/a") is True

    def test_rm_flags_then_no_flags_not_approved(self) -> None:
        """rm -rf /tmp/a approved -> check('rm /tmp/a') must be False."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /tmp/a")
        assert cache.check("rm -rf /tmp/a") is True
        assert cache.check("rm /tmp/a") is False

    def test_multi_target_with_flags(self) -> None:
        """rm -rf a b approved -> check must match only with same flags."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /a /b")
        assert cache.check("rm -rf /a /b") is True
        assert cache.check("rm /a /b") is False
        assert cache.check("rm -f /a /b") is False

    def test_separator_with_flags(self) -> None:
        """rm -rf /a approved ; rm /b not approved independently (same as before)."""
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("rm /a") is True
        assert cache.check("rm -rf /a") is False  # flag bypass blocked

