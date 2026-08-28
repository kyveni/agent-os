from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from reportlab.pdfgen import canvas

from agentos.tools.builtin import media
from agentos.tools.types import SafeToolError, ToolContext, ToolError, current_tool_context


def _image_png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def _patch_fetch_transport(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    """Route media.py's httpx.AsyncClient through ``transport``, keeping kwargs."""

    real_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_async_client)


def _patch_ssrf_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mocked host is fake; let SSRF validation see a public address."""

    import socket

    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "images.example.com":
            return real_getaddrinfo("93.184.216.34", *args, **kwargs)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _write_pdf(path: Path) -> None:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(240, 160))
    pdf.drawString(32, 120, "Accuracy")
    pdf.rect(40, 30, 40, 70, fill=1)
    pdf.rect(100, 30, 40, 95, fill=1)
    pdf.save()
    path.write_bytes(buffer.getvalue())


@pytest.mark.asyncio
async def test_image_tool_renders_workspace_pdf_before_vision_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "figure.pdf"
    _write_pdf(pdf_path)
    seen: dict[str, str] = {}

    async def fake_vision(b64_data: str, media_type: str, prompt: str) -> str:
        seen["media_type"] = media_type
        seen["prompt"] = prompt
        seen["payload_prefix"] = b64_data[:16]
        return "rendered chart"

    monkeypatch.setattr(media, "_call_vision_provider", fake_vision)

    token = current_tool_context.set(ToolContext(workspace_dir=str(tmp_path)))
    try:
        result = json.loads(await media.image("/workspace/figure.pdf", "describe the chart"))
    finally:
        current_tool_context.reset(token)

    assert result["description"] == "rendered chart"
    assert result["path"] == "/workspace/figure.pdf"
    assert seen == {
        "media_type": "image/png",
        "prompt": "describe the chart",
        "payload_prefix": seen["payload_prefix"],
    }
    assert seen["payload_prefix"]


@pytest.mark.asyncio
async def test_image_tool_reports_attachment_display_name_as_safe_path_error(
    tmp_path: Path,
) -> None:
    token = current_tool_context.set(ToolContext(workspace_dir=str(tmp_path)))
    try:
        with pytest.raises(SafeToolError) as exc_info:
            await media.image(
                "ab367eca88278bd6905ff705e3fee0b2907b86fbda389d9ed3f9c9d86f4603f5.png",
                "describe this image",
            )
    finally:
        current_tool_context.reset(token)

    message = exc_info.value.user_message
    assert "not accessible by the image tool" in message
    assert "local file path or HTTP(S) URL" in message
    assert "chat attachment" in message


@pytest.mark.asyncio
async def test_image_tool_reports_unsupported_format_as_safe_error(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("not an image", encoding="utf-8")

    token = current_tool_context.set(ToolContext(workspace_dir=str(tmp_path)))
    try:
        with pytest.raises(SafeToolError) as exc_info:
            await media.image("notes.txt", "describe this image")
    finally:
        current_tool_context.reset(token)

    assert "Unsupported image format" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_image_tool_reports_corrupt_image_as_safe_error(tmp_path: Path) -> None:
    source = tmp_path / "broken.png"
    source.write_bytes(b"not a png")

    token = current_tool_context.set(ToolContext(workspace_dir=str(tmp_path)))
    try:
        with pytest.raises(SafeToolError) as exc_info:
            await media.image("broken.png", "describe this image")
    finally:
        current_tool_context.reset(token)

    assert "corrupt or unreadable" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_fetch_image_url_rejects_oversized_content_length_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared Content-Length over 20MB is refused without reading the body."""
    _patch_ssrf_resolution(monkeypatch)
    body_read = False

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            nonlocal body_read
            body_read = True
            yield b"x" * 1024

        return httpx.Response(
            200,
            headers={
                "content-type": "image/png",
                "content-length": str(media._IMAGE_SIZE_LIMIT + 1),
            },
            content=body(),
        )

    _patch_fetch_transport(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(ToolError, match="20MB size limit"):
        await media._fetch_image_url("https://images.example.com/too-big.png")

    assert not body_read, "response body was read despite the oversized Content-Length"


@pytest.mark.asyncio
async def test_fetch_image_url_aborts_stream_when_body_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chunked body with no Content-Length is cut off once it passes 20MB."""
    _patch_ssrf_resolution(monkeypatch)

    async def endless_body():
        chunk = b"x" * (1024 * 1024)
        while True:
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "transfer-encoding": "chunked"},
            content=endless_body(),
        )

    _patch_fetch_transport(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(ToolError, match="20MB size limit"):
        await media._fetch_image_url("https://images.example.com/unbounded.png")


@pytest.mark.asyncio
async def test_fetch_image_url_streams_normal_image_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An image under the limit streams through intact."""
    _patch_ssrf_resolution(monkeypatch)
    png_bytes = _image_png_bytes()
    chunks = [png_bytes[i : i + 7] for i in range(0, len(png_bytes), 7)]

    async def body():
        for chunk in chunks:
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=body(),
        )

    _patch_fetch_transport(monkeypatch, httpx.MockTransport(handler))

    image_bytes, media_type = await media._fetch_image_url(
        "https://images.example.com/ok.png"
    )

    assert image_bytes == png_bytes
    assert media_type == "image/png"
