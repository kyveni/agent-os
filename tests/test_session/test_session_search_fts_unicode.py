"""Tests for FTS5 query sanitizer — must preserve non-ASCII Unicode letters."""

from agentos.session.storage import SessionStorage


class TestFtsSanitizerUnicode:
    """Verify that sanitize_fts_query preserves CJK, accented, and other
    non-ASCII alphabetic characters that the old ASCII-only regex stripped."""

    @staticmethod
    def sanitize(raw: str) -> str:
        return SessionStorage.sanitize_fts_query(raw)

    def test_ascii_alphanumeric(self):
        """Plain ASCII words still work."""
        result = self.sanitize("hello world")
        assert result == '"hello" "world"', f"got {result!r}"

    def test_cjk_ideographs(self):
        """CJK characters are preserved."""
        result = self.sanitize("你好世界")
        assert result == '"你好世界"', f"got {result!r}"

    def test_chinese_phrase_with_spaces(self):
        """Chinese phrase with spaces produces multiple tokens."""
        result = self.sanitize("搜索 查询")
        assert result == '"搜索" "查询"', f"got {result!r}"

    def test_accented_latin(self):
        """Accented Latin letters like é, ñ, ü are preserved."""
        result = self.sanitize("café piñata Über")
        assert result == '"café" "piñata" "Über"', f"got {result!r}"

    def test_cyrillic(self):
        """Cyrillic letters are preserved."""
        result = self.sanitize("привет мир")
        assert result == '"привет" "мир"', f"got {result!r}"

    def test_korean_hangul(self):
        """Hangul syllables are preserved."""
        result = self.sanitize("안녕 세상")
        assert result == '"안녕" "세상"', f"got {result!r}"

    def test_arabic(self):
        """Arabic script letters are preserved."""
        result = self.sanitize("مرحبا بالعالم")
        assert result == '"مرحبا" "بالعالم"', f"got {result!r}"

    def test_mixed_ascii_and_unicode(self):
        """Mixed ASCII + non-ASCII tokens."""
        result = self.sanitize("hello 世界 café")
        assert result == '"hello" "世界" "café"', f"got {result!r}"

    def test_operators_stripped(self):
        """FTS5 operators like AND, OR, NOT, parentheses are stripped."""
        result = self.sanitize("hello AND world OR (test)")
        assert result == '"hello" "AND" "world" "OR" "test"', f"got {result!r}"

    def test_special_chars_stripped(self):
        """Punctuation and symbols are stripped regardless of script."""
        result = self.sanitize("hello! @world #café?")
        assert result == '"hello" "world" "café"', f"got {result!r}"

    def test_empty_input(self):
        """Empty input returns empty-string quoted pair."""
        assert self.sanitize("") == '""'
        assert self.sanitize("   ") == '""'

    def test_token_limit(self):
        """At most 20 tokens are returned."""
        tokens = " ".join(f"word{i}" for i in range(30))
        result = self.sanitize(tokens)
        assert len(result.split()) == 20

    def test_unicode_token_limit(self):
        """Unicode tokens also respect the 20-token cap."""
        tokens = " ".join(f"词{i}" for i in range(25))
        result = self.sanitize(tokens)
        assert len(result.split()) == 20

    def test_japanese_mixed_scripts(self):
        """Japanese hiragana, katakana, and kanji are all preserved."""
        result = self.sanitize("こんにちは カタカナ 東京")
        assert result == '"こんにちは" "カタカナ" "東京"', f"got {result!r}"

    def test_vietnamese(self):
        """Vietnamese with many diacritics is preserved."""
        result = self.sanitize("Việt Nam tiếng Việt")
        assert all(t.strip('"') for t in result.split()), f"got {result!r}"
        assert "Việt" in result
        assert "tiếng" in result
