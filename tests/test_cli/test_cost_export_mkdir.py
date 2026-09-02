"""`agentos cost --export` creates parent directories automatically."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentos.cli.main import app

runner = CliRunner()

FAKE_PAYLOAD: dict[str, Any] = {
    "totalCostUsd": 0.00123,
    "breakdown": [
        {
            "session": "s1",
            "model": "gpt-4",
            "provider": "openai",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.00123,
            "created_at": 1700000000,
        },
    ],
}


def _fake_run(*args: Any, **kwargs: Any) -> Any:
    return FAKE_PAYLOAD


def test_export_creates_nested_parent_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cost --export should mkdir parent dirs when they don't exist."""
    monkeypatch.setattr("agentos.cli.cost_cmd.run_gateway_sync", _fake_run)

    deep_path = tmp_path / "reports" / "october" / "costs.json"
    assert not deep_path.parent.exists()

    result = runner.invoke(app, ["cost", "--export", str(deep_path)])

    assert result.exit_code == 0, result.output
    assert deep_path.exists()
    data = json.loads(deep_path.read_text(encoding="utf-8"))
    assert data["totalCostUsd"] == 0.00123


def test_export_works_with_existing_parent_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing parent dirs are still fine (idempotent)."""
    monkeypatch.setattr("agentos.cli.cost_cmd.run_gateway_sync", _fake_run)

    path = tmp_path / "costs.json"
    result = runner.invoke(app, ["cost", "--export", str(path)])

    assert result.exit_code == 0, result.output
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["totalCostUsd"] == 0.00123


def test_export_csv_creates_nested_parent_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CSV export with non-existent parent dirs also works."""
    monkeypatch.setattr("agentos.cli.cost_cmd.run_gateway_sync", _fake_run)

    deep_path = tmp_path / "a" / "b" / "costs.csv"
    assert not deep_path.parent.exists()

    result = runner.invoke(app, ["cost", "--export", str(deep_path)])

    assert result.exit_code == 0, result.output
    assert deep_path.exists()
    content = deep_path.read_text(encoding="utf-8")
    assert "Session" in content
    assert "s1" in content
