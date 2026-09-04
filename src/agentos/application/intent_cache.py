"""Session-scoped cache of approved action intents.

The per-approval queue treats every tool invocation as a fresh request. That
means approving ``rm /tmp/x`` does nothing for a subsequent
``os.remove("/tmp/x")`` or ``Path("/tmp/x").unlink()`` — the model can paraphrase
its way past approval prompts and the user has to press y repeatedly. This
module normalizes destructive actions to a semantic key (intent kind + target)
and remembers approvals for a short window, so paraphrased retries of the same
intent proceed without another prompt.

Scope: only *delete* intents for now, since that is the bulk of user-observed
pain. Extend ``_extract_intent`` if other classes (write-outside-workspace,
network egress) need intent-level memory.
"""

from __future__ import annotations

import os
import re
import shlex
import threading
import time
from pathlib import Path

_DEFAULT_TTL_SECONDS = 30 * 60
_ALWAYS_TTL_SECONDS = 365 * 24 * 3600  # effectively never expires within a session

# Escalation capability constants.
# Each represents a strictly more powerful permission than a bare delete.
CAP_RECURSIVE = "recursive"         # -r / -R / --recursive / shutil.rmtree / os.removedirs
CAP_FORCE = "force"                 # -f / --force
CAP_NO_PRESERVE_ROOT = "npr"        # --no-preserve-root

# Mapping: short-flag char -> escalation capabilities
_SHORT_FLAG_CAPS: dict[str, frozenset[str]] = {
    "r": frozenset({CAP_RECURSIVE}),
    "R": frozenset({CAP_RECURSIVE}),
    "f": frozenset({CAP_FORCE}),
}

# Mapping: long-flag string -> escalation capabilities
_LONG_FLAG_CAPS: dict[str, frozenset[str]] = {
    "--recursive": frozenset({CAP_RECURSIVE}),
    "--force": frozenset({CAP_FORCE}),
    "--no-preserve-root": frozenset({CAP_NO_PRESERVE_ROOT}),
}

# Python delete functions that carry recursive capability.
_PY_RECURSIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bshutil\.rmtree\s*\(\s*[\"']([^\"']+)[\"']"),
    re.compile(r"\bos\.removedirs\s*\(\s*[\"']([^\"']+)[\"']"),
)

# Python delete functions that do NOT carry recursive capability.
_PY_NON_RECURSIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bos\.(?:remove|unlink|rmdir)\s*\(\s*[\"']([^\"']+)[\"']"),
    re.compile(
        r"\b(?:pathlib\.)?Path\s*\(\s*[\"']([^\"']+)[\"']\s*\)\s*\.(?:unlink|rmdir)\s*\("
    ),
)

# Shell command separators that terminate a single ``rm`` invocation.
_SHELL_SEPARATORS = (";", "&&", "||", "|", "&")


def _norm_path(raw: str, *, base_dir: str | Path | None = None) -> str:
    """Best-effort absolute-path normalization.

    Leaves non-path tokens alone (so ``*`` or variable references don't get
    expanded into something wrong).
    """
    if not raw or raw.startswith(("$", "`")) or raw in {"*", "-"}:
        return raw
    try:
        path = Path(raw).expanduser()
        if base_dir is not None and not path.is_absolute():
            path = Path(base_dir).expanduser() / path
        return str(path.resolve(strict=False))
    except (OSError, ValueError):
        return raw


def _extract_rm_invocation_caps(tail: str) -> frozenset[str]:
    """Extract escalation capabilities from one ``rm`` invocation tail.

    ``tail`` is everything after ``rm`` up to the next shell separator.
    Only flags that precede the first positional argument are considered
    (standard ``rm`` convention).
    """
    caps: set[str] = set()
    try:
        tokens = shlex.split(tail)
    except ValueError:
        tokens = tail.split()

    for token in tokens:
        if not token.startswith("-"):
            break  # positional args start; stop scanning for flags
        if token == "--":
            break  # end-of-options marker
        if token.startswith("--"):
            caps.update(_LONG_FLAG_CAPS.get(token, frozenset()))
        else:
            # Short flags: -rf -> -r, -f
            for ch in token.lstrip("-"):
                caps.update(_SHORT_FLAG_CAPS.get(ch, frozenset()))
    return frozenset(caps)


def _extract_rm_targets(command: str) -> list[tuple[str, frozenset[str]]]:
    """Pull every ``(target, caps)`` pair from every ``rm`` invocation.

    Each ``rm`` invocation is parsed independently with its own flags.
    Handles ``rm a b c``, ``rm -rf /a /b``, quoted paths, and stops at shell
    separators. Uses ``finditer`` so ``rm foo; rm -rf /bar`` yields flags
    scoped per invocation (not leaked across).
    """
    pattern = re.compile(r"\brm\b([^;\n&|]*)")
    matches = list(pattern.finditer(command))
    if not matches:
        return []

    results: list[tuple[str, frozenset[str]]] = []
    seen_targets: set[str] = set()

    for match in matches:
        tail = match.group(1).strip()
        if not tail:
            continue
        rm_caps = _extract_rm_invocation_caps(tail)

        token_sets: list[list[str]] = []
        try:
            token_sets.append(shlex.split(tail))
        except ValueError:
            token_sets.append(tail.split())
        if "\\" in tail and (os.name == "nt" or re.search(r"(?:^|\s)\\[^\s]", tail)):
            try:
                token_sets.append(shlex.split(tail, posix=False))
            except ValueError:
                token_sets.append(tail.split())

        for tokens in token_sets:
            for token in tokens:
                if not token or token.startswith("-") or token in _SHELL_SEPARATORS \
                    or token == "--":
                    continue
                if token in seen_targets:
                    continue
                seen_targets.add(token)
                results.append((token, rm_caps))

    return results


def _extract_intents(
    command: str,
    *,
    base_dir: str | Path | None = None,
) -> list[tuple[str, frozenset[str], str]]:
    """Return every recognized destructive intent with escalation capabilities.

    Each item is ``(kind, caps, target)`` where *caps* is a frozenset of
    escalation capabilities (e.g. ``frozenset({"recursive", "force"})``).

    Shell ``rm``:
        ``rm -rf /a /b`` → two intents, each with ``{"recursive", "force"}``.

    Python deletes:
        ``shutil.rmtree("/a")`` → ``{"recursive"}``.
        ``os.remove("/a")`` → empty caps (plain delete).
        ``os.removedirs("/a")`` → ``{"recursive"}``.

    Subset rule in :meth:`IntentApprovalCache.check`: ``requested ⊆ approved``,
    so approving a plain delete does NOT satisfy a recursive+force request.
    """
    if not command:
        return []

    # Track caps per target, merging from rm invocations and Python patterns.
    caps_by_target: dict[str, frozenset[str]] = {}

    # Shell rm invocations.
    for target, caps in _extract_rm_targets(command):
        existing = caps_by_target.get(target, frozenset())
        caps_by_target[target] = existing | caps

    # Python recursive+force deletes (shutil.rmtree — inherently forced).
    for pattern in _PY_RECURSIVE_FORCE_PATTERNS:
        for m in pattern.finditer(command):
            target = m.group(1)
            existing = caps_by_target.get(target, frozenset())
            caps_by_target[target] = existing | frozenset({CAP_RECURSIVE, CAP_FORCE})

    # Python recursive-only deletes (os.removedirs — removes empty dirs recursively).
    for pattern in _PY_RECURSIVE_ONLY_PATTERNS:
        for m in pattern.finditer(command):
            target = m.group(1)
            existing = caps_by_target.get(target, frozenset())
            caps_by_target[target] = existing | frozenset({CAP_RECURSIVE})

    # Python non-recursive deletes (os.remove, Path.unlink, etc.).
    for pattern in _PY_NON_RECURSIVE_PATTERNS:
        for m in pattern.finditer(command):
            target = m.group(1)
            caps_by_target.setdefault(target, frozenset())

    # Build deduplicated result.
    result: list[tuple[str, frozenset[str], str]] = []
    seen: set[tuple[str, frozenset[str], str]] = set()
    for raw, caps in caps_by_target.items():
        intent = ("delete", caps, _norm_path(raw, base_dir=base_dir))
        if intent in seen:
            continue
        seen.add(intent)
        result.append(intent)
    return result


def _extract_intent(command: str) -> tuple[str, frozenset[str], str] | None:
    """First extracted intent, or None. Convenience for single-target callers."""
    intents = _extract_intents(command)
    return intents[0] if intents else None


def _caps_display(caps: frozenset[str]) -> str:
    """Human-readable representation of escalation capabilities for display."""
    parts = []
    if CAP_RECURSIVE in caps:
        parts.append("-r")
    if CAP_FORCE in caps:
        parts.append("-f")
    if CAP_NO_PRESERVE_ROOT in caps:
        parts.append("--no-preserve-root")
    return " ".join(parts) if parts else ""


class IntentApprovalCache:
    """In-memory cache keyed by ``(kind, target, scope)`` with escalation caps.

    Two scopes exist so the approval prompt's ``once`` and ``always`` mean
    what they say:

    * ``once``  — covers only paraphrased retries within the same user turn
                  (rm → os.remove within one model response). Cleared at the
                  start of every new user message via :meth:`clear_scope`.
    * ``always`` — persists for the full session TTL; re-prompts won't appear
                  for the same intent until the process restarts.

    Escalation capabilities are tracked **per scope** and **monotonically**:
    if a ``once`` approval for ``rm -rf /a`` is followed by a ``once``
    approval for ``rm /a`` (weaker caps), the entry retains ``{recursive,
    force}``. ``check()`` uses a **subset rule**: ``requested_caps ⊆
    stored_caps``. This means approving ``rm /tmp/a`` (no caps) does NOT
    satisfy ``rm -rf /tmp/a`` (recursive+force), while approving ``rm -rf
    /tmp/a`` does satisfy ``rm /tmp/a``.
    """

    def __init__(self, default_ttl: float = _DEFAULT_TTL_SECONDS) -> None:
        self._default_ttl = default_ttl
        # (kind, target, scope) -> (caps: frozenset[str], expires: float)
        self._entries: dict[tuple[str, str, str], tuple[frozenset[str], float]] = {}
        self._lock = threading.Lock()

    def record(
        self, command: str, ttl: float | None = None, *, scope: str = "once"
    ) -> list[tuple[str, frozenset[str], str]]:
        """Mark every intent extracted from *command* as approved.

        Handles multi-target commands like ``rm a b c`` — each path becomes its
        own cache entry. Escalation capabilities are unioned monotonically
        within each scope, so approving weaker caps does not downgrade an
        existing entry.

        Returns the list of recorded intents (empty if none could be extracted).
        """
        intents = _extract_intents(command)
        if not intents:
            return []
        expires = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            for kind, caps, target in intents:
                key = (kind, target, scope)
                existing = self._entries.get(key)
                if existing:
                    stored_caps, stored_expires = existing
                    caps = caps | stored_caps  # monotonic union
                    expires = max(expires, stored_expires)
                self._entries[key] = (caps, expires)
        return intents

    def record_always(self, command: str) -> list[tuple[str, frozenset[str], str]]:
        """Remember every intent in *command* for the session lifetime."""
        return self.record(command, ttl=_ALWAYS_TTL_SECONDS, scope="always")

    def check(self, command: str) -> bool:
        """Return True only when **every** extracted intent is still approved.

        Multi-target commands must have approval for *all* targets — one
        missing path means the whole command needs fresh approval.

        The **subset rule** applies: an intent is approved if there exists a
        stored entry with the same ``(kind, target)`` whose caps are a superset
        of (or equal to) the requested caps, and the entry hasn't expired.
        """
        intents = _extract_intents(command)
        if not intents:
            return False
        now = time.monotonic()
        with self._lock:
            for kind, caps, target in intents:
                approved = False
                # Scan across all scope entries for this (kind, target).
                for (ekind, etarget, escope), (ecaps, eexpires) in list(self._entries.items()):
                    if ekind != kind or etarget != target:
                        continue
                    if eexpires < now:
                        self._entries.pop((ekind, etarget, escope), None)
                        continue
                    if caps <= ecaps:
                        approved = True
                        break
                if not approved:
                    return False
        return True

    def forget(self, command: str) -> None:
        intents = _extract_intents(command)
        if not intents:
            return
        with self._lock:
            for kind, caps, target in intents:
                # Remove all scope entries for this (kind, target).
                keys_to_remove = [
                    k for k in self._entries
                    if k[0] == kind and k[1] == target
                ]
                for k in keys_to_remove:
                    self._entries.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def clear_scope(self, scope: str) -> None:
        """Drop every entry whose scope matches, leaving other scopes intact."""
        with self._lock:
            self._entries = {
                (kind, target, s): data
                for (kind, target, s), data in self._entries.items()
                if s != scope
            }


_cache: IntentApprovalCache | None = None


def get_intent_cache() -> IntentApprovalCache:
    global _cache
    if _cache is None:
        _cache = IntentApprovalCache()
    return _cache


def reset_intent_cache() -> None:
    """Test hook — drop the singleton."""
    global _cache
    _cache = None
