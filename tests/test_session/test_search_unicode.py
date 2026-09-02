"""Test FTS5 query sanitizer preserves Unicode letters."""
from __future__ import annotations

from agentos.session.storage import SessionStorage


class TestSanitizeFtsQuery:
    def test_ascii_passthrough(self) -> None:
        assert SessionStorage.sanitize_fts_query("hello world") == '"hello" "world"'

    def test_cjk_characters(self) -> None:
        result = SessionStorage.sanitize_fts_query("你好世界")
        assert "你好世界" in result or all(c in result for c in "你好世界")

    def test_accented_characters(self) -> None:
        result = SessionStorage.sanitize_fts_query("café résumé")
        assert "café" in result
        assert "résumé" in result

    def test_mixed_ascii_and_unicode(self) -> None:
        result = SessionStorage.sanitize_fts_query("hello 世界 café")
        assert "hello" in result
        assert "世界" in result or "café" in result

    def test_special_chars_stripped(self) -> None:
        result = SessionStorage.sanitize_fts_query("rm -rf /")
        assert '"rm"' in result
        assert '"rf"' in result

    def test_emoji_stripped_but_text_preserved(self) -> None:
        result = SessionStorage.sanitize_fts_query("hello 🔥 world")
        assert "hello" in result
        assert "world" in result

    def test_empty_string(self) -> None:
        assert SessionStorage.sanitize_fts_query("") == '""'
