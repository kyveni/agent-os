"""Comprehensive regression tests for IntentApprovalCache with escalation caps.

Validates that the subset-rule design from #854 (v2) closes all bypass vectors
identified by @andreapn in the review of v1:

1.  ``sudo rm -rf /tmp/a``    — command prefix before rm
2.  ``true; rm -rf /tmp/a``   — shell separator before rm
3.  ``rm --recursive --force`` — long flags
4.  ``shutil.rmtree('...')``  — Python recursive delete
5.  Cross-invocation flag leak (``rm -rf /a; rm /b`` → ``rm -rf /b``)
6.  ``-f`` / ``--force`` tracking (not just recursive)
7.  ``os.rmdir`` / ``Path.rmdir`` are NOT recursive (v1 inverted this)
8.  Scope leak: ``once`` cannot permanently upgrade ``always``
"""

from __future__ import annotations

from agentos.application.intent_cache import (
    CAP_FORCE,
    CAP_NO_PRESERVE_ROOT,
    CAP_RECURSIVE,
    IntentApprovalCache,
    _extract_intents,
    reset_intent_cache,
)


# ---------------------------------------------------------------------------
# Escalation capability extractor tests
# ---------------------------------------------------------------------------

class TestExtractIntents:
    """Validate the shape of extracted intents."""

    def test_plain_rm_has_no_caps(self) -> None:
        intents = _extract_intents("rm /tmp/a")
        assert intents == [("delete", frozenset(), "/tmp/a")]

    def test_rm_rf_has_recursive_force(self) -> None:
        intents = _extract_intents("rm -rf /tmp/a")
        assert len(intents) == 1
        kind, caps, target = intents[0]
        assert kind == "delete"
        assert target == "/tmp/a"
        assert CAP_RECURSIVE in caps
        assert CAP_FORCE in caps

    def test_rm_r_has_recursive_only(self) -> None:
        intents = _extract_intents("rm -r /tmp/a")
        assert intents == [("delete", frozenset({CAP_RECURSIVE}), "/tmp/a")]

    def test_rm_f_has_force_only(self) -> None:
        intents = _extract_intents("rm -f /tmp/a")
        assert intents == [("delete", frozenset({CAP_FORCE}), "/tmp/a")]

    def test_long_flags_recursive_force(self) -> None:
        intents = _extract_intents("rm --recursive --force /tmp/a")
        assert len(intents) == 1
        _, caps, _ = intents[0]
        assert CAP_RECURSIVE in caps
        assert CAP_FORCE in caps

    def test_long_flag_no_preserve_root(self) -> None:
        intents = _extract_intents("rm -rf --no-preserve-root /tmp/a")
        assert len(intents) == 1
        _, caps, _ = intents[0]
        assert CAP_NO_PRESERVE_ROOT in caps

    def test_multi_target_same_caps(self) -> None:
        intents = _extract_intents("rm -rf /a /b /c")
        assert len(intents) == 3
        for (kind, caps, target) in intents:
            assert kind == "delete"
            assert CAP_RECURSIVE in caps
            assert CAP_FORCE in caps
        targets = {t for _, _, t in intents}
        assert targets == {"/a", "/b", "/c"}

    def test_shutil_rmtree_is_recursive(self) -> None:
        intents = _extract_intents('shutil.rmtree("/tmp/a")')
        assert intents == [("delete", frozenset({CAP_RECURSIVE}), "/tmp/a")]

    def test_os_removedirs_is_recursive(self) -> None:
        intents = _extract_intents('os.removedirs("/tmp/a")')
        assert intents == [("delete", frozenset({CAP_RECURSIVE}), "/tmp/a")]

    def test_os_remove_is_not_recursive(self) -> None:
        intents = _extract_intents('os.remove("/tmp/a")')
        assert intents == [("delete", frozenset(), "/tmp/a")]

    def test_os_unlink_is_not_recursive(self) -> None:
        intents = _extract_intents('os.unlink("/tmp/a")')
        assert intents == [("delete", frozenset(), "/tmp/a")]

    def test_os_rmdir_is_not_recursive(self) -> None:
        intents = _extract_intents('os.rmdir("/tmp/a")')
        assert intents == [("delete", frozenset(), "/tmp/a")]

    def test_path_unlink_is_not_recursive(self) -> None:
        intents = _extract_intents('Path("/tmp/a").unlink()')
        assert intents == [("delete", frozenset(), "/tmp/a")]

    def test_path_rmdir_is_not_recursive(self) -> None:
        intents = _extract_intents('Path("/tmp/a").rmdir()')
        assert intents == [("delete", frozenset(), "/tmp/a")]

    def test_sudo_rm_extracts_caps(self) -> None:
        """sudo prefix must not prevent flag extraction."""
        intents = _extract_intents("sudo rm -rf /tmp/a")
        assert len(intents) == 1
        _, caps, _ = intents[0]
        assert CAP_RECURSIVE in caps
        assert CAP_FORCE in caps

    def test_cross_invocation_flags_isolated(self) -> None:
        """Flags from rm -rf /a must NOT leak to rm /b."""
        intents = _extract_intents("rm -rf /a; rm /b")
        assert len(intents) == 2
        targets_caps = {t: c for _, c, t in intents}
        assert CAP_RECURSIVE in targets_caps["/a"]
        assert CAP_FORCE in targets_caps["/a"]
        assert targets_caps["/b"] == frozenset()

    def test_empty_command_returns_empty(self) -> None:
        assert _extract_intents("") == []

    def test_non_delete_returns_empty(self) -> None:
        assert _extract_intents("echo hello") == []


# ---------------------------------------------------------------------------
# Cache record/check lifecycle with escalation caps
# ---------------------------------------------------------------------------

class TestRecordAndCheck:
    """Basic record/check lifecycle with subset-rule."""

    def test_plain_rm_roundtrip(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("rm /a") is True

    def test_empty_command_returns_false(self) -> None:
        cache = IntentApprovalCache()
        assert cache.check("") is False

    def test_non_rm_command_returns_false(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("echo hello") is False

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

    def test_record_always_survives_clear_scope_once(self) -> None:
        cache = IntentApprovalCache()
        cache.record_always("rm /a")
        cache.clear_scope("once")
        assert cache.check("rm /a") is True


# ---------------------------------------------------------------------------
# Subset-rule: approving weaker caps does NOT satisfy stronger caps
# ---------------------------------------------------------------------------

class TestSubsetRule:
    """Core security property: requested ⊆ approved."""

    def test_plain_delete_does_not_authorize_recursive(self) -> None:
        """CERT #849 primary vector: rm /tmp/a --X--> rm -rf /tmp/a."""
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("rm /tmp/a") is True
        assert cache.check("rm -rf /tmp/a") is False

    def test_plain_delete_does_not_authorize_force(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("rm -f /tmp/a") is False

    def test_recursive_authorizes_plain_delete(self) -> None:
        """Approving rm -rf /a should cover rm /a (subset: {} ⊆ {recursive})."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /tmp/a")
        assert cache.check("rm -rf /tmp/a") is True
        assert cache.check("rm /tmp/a") is True  # subset rule

    def test_recursive_does_not_authorize_recursive_force(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -r /tmp/a")
        assert cache.check("rm -r /tmp/a") is True
        assert cache.check("rm -rf /tmp/a") is False  # force not approved

    def test_force_does_not_authorize_recursive(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -f /tmp/a")
        assert cache.check("rm -f /tmp/a") is True
        assert cache.check("rm -rf /tmp/a") is False

    def test_multi_target_with_subset_caps(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -rf /a /b")
        assert cache.check("rm -rf /a /b") is True
        assert cache.check("rm /a /b") is True  # subset
        assert cache.check("rm -rf /a /b /c") is False  # extra target


# ---------------------------------------------------------------------------
# Andreapn's bypass vectors from the review (all must be closed)
# ---------------------------------------------------------------------------

class TestAndreapnBypassVectors:
    """Every bypass vector @andreapn identified in v1 review."""

    def test_sudo_rm_rf_not_approved_by_plain_rm(self) -> None:
        """sudo rm -rf /tmp/a must NOT match rm /tmp/a."""
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("sudo rm -rf /tmp/a") is False

    def test_shell_separator_rm_rf_not_approved_by_plain_rm(self) -> None:
        """true; rm -rf /tmp/a must NOT match rm /tmp/a."""
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("true; rm -rf /tmp/a") is False

    def test_long_flags_not_approved_by_plain_rm(self) -> None:
        """rm --recursive --force /tmp/a must NOT match rm /tmp/a."""
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("rm --recursive --force /tmp/a") is False

    def test_shutil_rmtree_not_approved_by_plain_rm(self) -> None:
        """shutil.rmtree('/tmp/a') must NOT match rm /tmp/a."""
        cache = IntentApprovalCache()
        cache.record("rm /tmp/a")
        assert cache.check("shutil.rmtree('/tmp/a')") is False

    def test_cross_invocation_flag_leak_blocked(self) -> None:
        """rm -rf /a approved -> rm -rf /b must be False (never approved)."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /a; rm /b")
        assert cache.check("rm -rf /b") is False  # /b was only approved unflagged

    def test_plain_rm_covers_os_remove(self) -> None:
        """rm /a approved -> os.remove('/a') should be True (same caps)."""
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("os.remove('/a')") is True

    def test_plain_rm_does_not_cover_shutil_rmtree(self) -> None:
        """rm /a approved -> shutil.rmtree('/a') must be False."""
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("shutil.rmtree('/a')") is False

    def test_os_rmdir_does_not_grant_recursive(self) -> None:
        """os.rmdir('/a') approved -> rm -rf /a must be False."""
        cache = IntentApprovalCache()
        cache.record("os.rmdir('/a')")
        assert cache.check("os.rmdir('/a')") is True
        assert cache.check("rm -rf /a") is False

    def test_path_rmdir_does_not_grant_recursive(self) -> None:
        """Path('/a').rmdir() approved -> rm -rf /a must be False."""
        cache = IntentApprovalCache()
        cache.record("Path('/a').rmdir()")
        assert cache.check("Path('/a').rmdir()") is True
        assert cache.check("rm -rf /a") is False

    def test_rm_rf_approved_covers_same(self) -> None:
        """rm -rf /a approved -> shutil.rmtree('/a') should be True."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /a")
        assert cache.check("shutil.rmtree('/a')") is True

    def test_shutil_rmtree_approved_covers_rm_rf(self) -> None:
        """shutil.rmtree('/a') approved -> rm -rf /a should be True."""
        cache = IntentApprovalCache()
        cache.record("shutil.rmtree('/a')")
        assert cache.check("rm -rf /a") is True

    def test_os_removedirs_approved_covers_rm_r(self) -> None:
        """os.removedirs('/a') approved -> rm -r /a should be True."""
        cache = IntentApprovalCache()
        cache.record("os.removedirs('/a')")
        assert cache.check("rm -r /a") is True


# ---------------------------------------------------------------------------
# Cross-PR bypass vectors
# ---------------------------------------------------------------------------

class TestCompoundCommandSeparatorBypass:
    """Every shell separator must be caught by the permission cache."""

    def _check_separator(self, separator: str) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a")
        assert cache.check("rm /a") is True
        assert cache.check(f"rm /a{separator} rm /b") is False

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
        cache = IntentApprovalCache()
        cache.record("rm /a /b")
        assert cache.check("rm /a /b") is True

    def test_extra_target_not_approved_fails(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm /a /b")
        assert cache.check("rm /a /b /c") is False


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------

class TestScopeIsolation:
    """Scope-aware entries: 'once' and 'always' do not interfere."""

    def test_once_does_not_leak_to_always(self) -> None:
        """'once' entry should not satisfy a check without scope awareness
        but our check() scans all scopes. We verify that 'once' expiry
        doesn't contaminate 'always' by clearing once scope."""
        cache = IntentApprovalCache()
        cache.record("rm -rf /a", scope="once")
        assert cache.check("rm -rf /a") is True
        cache.clear_scope("once")
        assert cache.check("rm -rf /a") is False

    def test_always_survives_once_clear(self) -> None:
        """Clearing 'once' scope must not affect 'always' entries."""
        cache = IntentApprovalCache()
        cache.record_always("rm /a")
        cache.record("rm -rf /b", scope="once")
        cache.clear_scope("once")
        assert cache.check("rm /a") is True
        assert cache.check("rm -rf /b") is False

    def test_once_cannot_permanently_upgrade_always(self) -> None:
        """PR #853 review: once approval must not permanently upgrade always.
        
        A once approval for rm -rf /a should only survive its turn scope.
        After clear_scope("once"), the rm -rf /a check must fall back to
        whatever the 'always' entry had.
        """
        cache = IntentApprovalCache()
        cache.record_always("rm /a")           # user picks "always" for plain delete
        cache.record("rm -rf /a", scope="once")  # user picks "once" for recursive
        assert cache.check("rm -rf /a") is True   # both scopes active
        cache.clear_scope("once")                 # new turn
        # The "always" entry is (frozenset(), expires) — no caps.
        # caps {recursive, force} is NOT a subset of frozenset().
        assert cache.check("rm -rf /a") is False, (
            "once-scoped recursive grant must not persist into the always entry"
        )
        # Plain delete still works because always had no caps
        assert cache.check("rm /a") is True


# ---------------------------------------------------------------------------
# Monotonic caps union
# ---------------------------------------------------------------------------

class TestMonotonicCaps:
    """Approving weaker caps must not downgrade an existing entry."""

    def test_monotonic_union(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -rf /a")      # caps = {recursive, force}
        cache.record("rm /a")          # weaker caps, should not downgrade
        assert cache.check("rm -rf /a") is True  # still has recursive + force
        assert cache.check("rm -f /a") is True    # force subset still OK


# ---------------------------------------------------------------------------
# Flag normalization across long/short variants
# ---------------------------------------------------------------------------

class TestFlagVariants:
    """Different flag spellings that produce equivalent caps."""

    def test_short_and_long_flags_equivalent(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -rf /a")
        assert cache.check("rm --recursive --force /a") is True

    def test_separated_flags_equivalent(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -r -f /a")
        assert cache.check("rm -rf /a") is True

    def test_reordered_caps_equivalent(self) -> None:
        cache = IntentApprovalCache()
        cache.record("rm -rf /a")
        assert cache.check("rm -f -r /a") is True