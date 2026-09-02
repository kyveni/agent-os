from __future__ import annotations

from pathlib import Path

import pytest

from agentos.tools.builtin import filesystem as fs


@pytest.mark.asyncio
async def test_list_dir_skips_broken_symlink(tmp_path: Path) -> None:
    """list_dir should skip dangling/broken symlinks instead of crashing."""
    real_file = tmp_path / "real.txt"
    real_file.write_text("hello", encoding="utf-8")

    broken_link = tmp_path / "broken.link"
    broken_link.symlink_to(tmp_path / "nonexistent_target.txt")

    output = await fs.list_dir(str(tmp_path))

    assert "[file] real.txt" in output
    assert "broken.link" not in output


@pytest.mark.asyncio
async def test_list_dir_handles_all_broken_symlinks(tmp_path: Path) -> None:
    """list_dir should still work when every entry is a broken symlink."""
    for i in range(3):
        link = tmp_path / f"link_{i}.link"
        link.symlink_to(tmp_path / f"nonexistent_{i}")

    output = await fs.list_dir(str(tmp_path))

    assert output == f"{tmp_path}: (empty directory)"


@pytest.mark.asyncio
async def test_list_dir_mixed_broken_and_valid(tmp_path: Path) -> None:
    """list_dir returns valid entries while skipping broken symlinks."""
    valid_file = tmp_path / "keep.txt"
    valid_file.write_text("keep", encoding="utf-8")
    valid_dir = tmp_path / "keep_dir"
    valid_dir.mkdir()

    broken_link = tmp_path / "gone.link"
    broken_link.symlink_to(tmp_path / "missing")

    output = await fs.list_dir(str(tmp_path))

    assert "[file] keep.txt" in output
    assert "[dir]  keep_dir/" in output
    assert "gone.link" not in output
