"""Tests that ``load_entries`` gracefully handles corrupted JSONL lines."""

from __future__ import annotations

import json
from pathlib import Path

from agentos.observability.decision_log import load_entries


def test_load_entries_skips_corrupted_jsonl(tmp_path: Path) -> None:
    """Corrupted / truncated JSONL lines produce a warning and are skipped."""
    log = tmp_path / "decisions-20260901.jsonl"

    valid = {
        "turn_id": "t1",
        "session_key": "s1",
        "prompt_hash": "a" * 16,
        "system_prompt_hash": "b" * 16,
        "tool_list_hash": "c" * 16,
        "tool_choice": "auto",
        "tokens_input": 100,
        "tokens_output": 10,
        "model": "test-model",
        "provider": "test",
        "latency_ms": 10,
        "ts": "2026-09-01T00:00:00Z",
        "savings": {},
    }

    # Write: valid line, a truncated JSON line, a garbled line, another valid line
    lines = [
        json.dumps(valid),
        '{"turn_id": "partial", "session_key": "s2", ... truncated',  # truncated JSON
        "not json at all {{{",
        json.dumps(valid),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    entries = load_entries(log)
    assert len(entries) == 2
    for e in entries:
        assert e.turn_id == "t1"


def test_load_entries_empty_file(tmp_path: Path) -> None:
    """An empty JSONL file produces an empty list."""
    log = tmp_path / "decisions-20260902.jsonl"
    log.write_text("", encoding="utf-8")
    assert load_entries(log) == []


def test_load_entries_missing_file(tmp_path: Path) -> None:
    """A nonexistent path produces an empty list."""
    log = tmp_path / "does-not-exist.jsonl"
    assert load_entries(log) == []


def test_load_entries_blank_lines(tmp_path: Path) -> None:
    """Blank lines are silently ignored."""
    log = tmp_path / "decisions-20260903.jsonl"
    valid = {
        "turn_id": "t1",
        "session_key": "s1",
        "prompt_hash": "a" * 16,
        "system_prompt_hash": "b" * 16,
        "tool_list_hash": "c" * 16,
        "tool_choice": "auto",
        "tokens_input": 100,
        "tokens_output": 10,
        "model": "test-model",
        "provider": "test",
        "latency_ms": 10,
        "ts": "2026-09-01T00:00:00Z",
        "savings": {},
    }
    lines = [
        "",
        "   ",
        json.dumps(valid),
        "",
        json.dumps(valid),
    ]
    log.write_text("\n".join(lines), encoding="utf-8")
    entries = load_entries(log)
    assert len(entries) == 2
