from __future__ import annotations

from pathlib import Path

from agentos.channels.artifact_delivery import _named_artifact_delivery_path


def test_named_artifact_delivery_path_uses_filename() -> None:
    source = Path(__file__)
    with _named_artifact_delivery_path(source, "report.txt") as delivery_path:
        assert delivery_path.name == "report.txt"
        assert delivery_path.exists()


def test_named_artifact_delivery_path_falls_back_for_empty_filename() -> None:
    source = Path(__file__)
    with _named_artifact_delivery_path(source, "") as delivery_path:
        assert delivery_path.name == "unnamed_artifact"
        assert delivery_path.exists()


def test_named_artifact_delivery_path_falls_back_for_dot_filename() -> None:
    source = Path(__file__)
    with _named_artifact_delivery_path(source, ".") as delivery_path:
        assert delivery_path.name == "unnamed_artifact"
        assert delivery_path.exists()
