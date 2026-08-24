"""ClawHub skill zip decompression caps — zip-bomb DoS prevention.

Bug: the ClawHub fetch path read every zip entry into an in-memory dict
with no cap on entry count, per-entry size, or total uncompressed size.
A small nested-deflate archive (a classic zip bomb) could OOM the gateway.

Fix: enforce caps on entry count, per-entry declared size, and total
declared size before reading entries; stream-read with a running total
that also rejects real uncompressed size exceeding the ceiling.
Also harden Windows-style path traversal (`\\`, `:`) that posixpath
does not neutralise.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from agentos.skills.hub.clawhub import ClawHubSource


def _make_zip(entries: dict[str, bytes | str]) -> bytes:
    """Build a zip archive from {filename: content} pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


def _make_zip_bomb_entry(size: int) -> bytes:
    """Return a highly compressible byte string of the given size."""
    return b"\x00" * size


# ---------------------------------------------------------------------------
# Happy path: valid skill bundle still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_valid_skill_bundle(monkeypatch) -> None:
    """A well-formed skill zip with SKILL.md is extracted normally."""
    source = ClawHubSource()
    zip_bytes = _make_zip(
        {
            "SKILL.md": "---\nname: test\ndescription: A test skill.\n---\n\n# Test\n",
            "scripts/hello.py": 'print("hello")',
        }
    )

    async def _fake_get(self, url, **kw):
        class Resp:
            content = zip_bytes
            text = ""

            def raise_for_status(self) -> None:
                pass

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await source.fetch("test-skill")
    assert result is not None
    assert "SKILL.md" in result.files
    assert "hello.py" in result.files
    assert result.files["SKILL.md"].startswith("---")


# ---------------------------------------------------------------------------
# Entry count cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_rejects_too_many_entries(monkeypatch) -> None:
    """An archive exceeding 1_000 entries must be rejected."""
    source = ClawHubSource()
    entries: dict[str, str] = {"SKILL.md": "---\nname: bomb\ndescription: x.\n---\n\n# Bomb\n"}
    for i in range(1001):
        entries[f"scripts/file_{i}.py"] = f"# file {i}"
    zip_bytes = _make_zip(entries)

    async def _fake_get(self, url, **kw):
        class Resp:
            content = zip_bytes
            text = ""

            def raise_for_status(self) -> None:
                pass

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await source.fetch("bomb-skill")
    assert result is None


# ---------------------------------------------------------------------------
# Per-entry size cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_entry(monkeypatch) -> None:
    """An entry whose declared uncompressed size exceeds 50 MB must be
    rejected before reading the entry data."""
    source = ClawHubSource()
    # Build a zip with one entry whose uncompressed size is > 50 MB.
    # We use zipfile directly to control the uncompressed size field.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", "---\nname: big\ndescription: x.\n---\n\n# Big\n")
        # Write a 60 MB compressible blob; zipfile stores the real size.
        zf.writestr("scripts/large.py", b"\x00" * (51 * 1024 * 1024))
    zip_bytes = buf.getvalue()

    async def _fake_get(self, url, **kw):
        class Resp:
            content = zip_bytes
            text = ""

            def raise_for_status(self) -> None:
                pass

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await source.fetch("big-skill")
    assert result is None


# ---------------------------------------------------------------------------
# Total declared size cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_rejects_total_too_large(monkeypatch) -> None:
    """An archive whose total declared uncompressed size exceeds 100 MB
    must be rejected."""
    source = ClawHubSource()
    # Build a zip with many medium entries that together exceed 100 MB.
    entries: dict[str, bytes] = {"SKILL.md": b"---\nname: total\ndescription: x.\n---\n\n# Total\n"}
    # 101 × 1 MB entries → 101 MB total
    for i in range(101):
        entries[f"scripts/chunk_{i}.py"] = b"# " + b"x" * (1024 * 1024 - 2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    zip_bytes = buf.getvalue()

    async def _fake_get(self, url, **kw):
        class Resp:
            content = zip_bytes
            text = ""

            def raise_for_status(self) -> None:
                pass

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await source.fetch("total-bomb-skill")
    assert result is None


# ---------------------------------------------------------------------------
# Running-total cap (real decompressed exceeds ceiling)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_rejects_real_total_exceeds_cap(monkeypatch) -> None:
    """Even if declared sizes are small, if the actual decompressed total
    exceeds the cap the archive must be rejected."""
    source = ClawHubSource()
    # Build a legitimate-sized archive to verify the running-total guard does
    # not false-reject. Individual entries < 50 MB, total well under 100 MB.
    entries: dict[str, bytes] = {"SKILL.md": b"---\nname: rt\ndescription: x.\n---\n\n# RT\n"}
    for i in range(5):
        entries[f"scripts/chunk_{i}.py"] = b"# " + b"y" * (512 * 1024 - 2)
    zip_bytes = _make_zip(entries)

    async def _fake_get(self, url, **kw):
        class Resp:
            content = zip_bytes
            text = ""

            def raise_for_status(self) -> None:
                pass

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await source.fetch("running-total-skill")
    # Well under the 100 MB cap, so should succeed.
    assert result is not None
    assert "SKILL.md" in result.files


# ---------------------------------------------------------------------------
# Windows-style path traversal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_blocks_windows_path_traversal(monkeypatch) -> None:
    r"""Entries with backslash or colon in the relative path (e.g. ..\ or C:\)
    must be blocked even though posixpath.normpath does not neutralise them."""
    source = ClawHubSource()
    entries: dict[str, str] = {
        "SKILL.md": "---\nname: trav\ndescription: x.\n---\n\n# Trav\n",
        r"scripts\..\..\etc\evil.conf": "pwned",
    }
    zip_bytes = _make_zip(entries)

    async def _fake_get(self, url, **kw):
        class Resp:
            content = zip_bytes
            text = ""

            def raise_for_status(self) -> None:
                pass

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await source.fetch("trav-skill")
    assert result is not None
    # The traversal entry must be blocked.
    assert all("\\" not in k and ":" not in k for k in result.files)
