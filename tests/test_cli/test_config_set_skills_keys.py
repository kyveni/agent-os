"""Tests for agentos config set with skills.config.* keys."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agentos.cli.main import app

runner = CliRunner()


def test_config_set_skills_config_keys_auto_vivifies(tmp_path: Path) -> None:
    """config set skills.config.wiki.prompt should auto-vivify missing intermediate dicts."""
    target = tmp_path / "test.toml"
    target.write_text("[skills]\nconfig = {}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["config", "set", "skills.config.wiki.prompt", "test-prompt", "--config", str(target)],
    )
    assert result.exit_code == 0, result.stdout

    check = runner.invoke(
        app,
        ["config", "get", "skills.config.wiki.prompt", "--config", str(target)],
    )
    assert check.exit_code == 0, check.stdout
    assert "test-prompt" in check.stdout

    check_all = runner.invoke(app, ["config", "get", "", "--config", str(target)])
    assert check_all.exit_code == 0, check_all.stdout
    assert "skills.config.wiki.prompt" in check_all.stdout

