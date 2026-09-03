from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentos.tools.builtin import filesystem as fs
from agentos.tools.types import CallerKind, ToolContext, current_tool_context


@contextmanager
def tool_context(workspace: Path) -> Iterator[None]:
    token = current_tool_context.set(
        ToolContext(
            caller_kind=CallerKind.CLI,
            channel_kind="cli",
            channel_id="cli:test",
            workspace_dir=str(workspace),
        )
    )
    try:
        yield
    finally:
        current_tool_context.reset(token)


@pytest.mark.asyncio
async def test_list_dir_handles_broken_symlink(tmp_path: Path) -> None:
    """list_dir should not crash on dangling symlinks."""
    target = tmp_path / "target_nonexistent"
    link = tmp_path / "broken.link"
    link.symlink_to(target)
    assert not link.exists(), "precondition: dangling symlink should not exist"

    normal_file = tmp_path / "normal.txt"
    normal_file.write_text("hello")

    normal_dir = tmp_path / "subdir"
    normal_dir.mkdir()

    with tool_context(tmp_path):
        output = await fs.list_dir(str(tmp_path))

    assert output is not None
    assert isinstance(output, str)

    result = output.split("\n")
    result.sort()

    assert any("broken.link" in r and "broken symlink" in r for r in result), (
        f"Expected 'broken symlink' marker in output, got: {result}"
    )
    assert any("normal.txt" in r for r in result), (
        f"Expected normal.txt in output, got: {result}"
    )
    assert any("subdir" in r for r in result), (
        f"Expected subdir in output, got: {result}"
    )
