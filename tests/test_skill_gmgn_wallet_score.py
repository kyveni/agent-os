"""Argument validation tests for score.py (gmgn-wallet-score / Copy-Trade Score).

score.py crashes with IndexError when invoked with fewer than 2 positional
arguments because it subscripted sys.argv[1] before checking the length.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "gmgn-wallet-score"
    / "scripts"
    / "score.py"
)


def run_score(*args: str) -> subprocess.CompletedProcess:
    """Run score.py with the given arguments and return the result."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestHelp:
    """score.py --help / -h / no-arg / insufficient-arg paths."""

    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_explicit_help_flag(self, flag: str) -> None:
        """Invoking with -h or --help prints the usage message and exits 0."""
        result = run_score(flag)
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        # Should not be grabbed by the earlier IndexError path
        assert result.stderr == ""

    def test_no_arguments(self) -> None:
        """Invoking with zero positional arguments prints help and exits 0."""
        result = run_score()
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert result.stderr == ""

    def test_only_one_argument(self) -> None:
        """Invoking with only a wallet address (no chain) prints help and exits 0."""
        result = run_score("Abc123...xyz")
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert result.stderr == ""

    def test_help_with_partial_args(self) -> None:
        """-h mixed with positional args still shows help."""
        result = run_score("--help", "Abc123...xyz", "sol")
        assert result.returncode == 0
        assert "Usage:" in result.stdout


class TestArglen:
    """Verify that providing enough args *would* reach the body rather than
    trigger the help-guard.  This is necessarily a smoke check because the
    script calls into gmgn-cli (unavailable in unit-test CI)."""

    def test_has_minimum_args(self) -> None:
        """With 2+ args the help guard exits quickly; we just confirm it parsed."""
        # Won't run far because gmgn-cli is missing, but we know it won't
        # crash with IndexError before even starting.
        result = run_score("0xWallet", "eth")
        # The script *will* fail — but via RuntimeError (gmgn-cli subprocess)
        # or similar, NOT via unhandled IndexError.
        assert result.returncode != 0
        assert "IndexError" not in result.stderr
        assert "score" in result.stdout.lower() or "Usage:" not in result.stdout

    def test_execution_path_not_help_stub(self) -> None:
        """With 2+ args the script does not just print help and bail."""
        result = run_score("0xWallet", "eth")
        assert "Usage:" not in result.stdout

    @pytest.mark.parametrize(
        "args",
        [
            (),
            ("-h",),
            ("--help",),
            ("wallet1",),
            ("-h", "wallet1", "sol"),
        ],
    )
    def test_never_indexerror(self, args: tuple[str, ...]) -> None:
        """No combination of zero-or-insufficient args should raise IndexError."""
        result = run_score(*args)
        assert "IndexError" not in result.stderr
