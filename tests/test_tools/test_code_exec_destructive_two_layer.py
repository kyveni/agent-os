"""Tests for two-layer destructive code detection in code_exec.py (#848).

Layer 1: regex on original code (fast path).
Layer 2: AST normalization + regex against normalized form.

Bypass scenarios from the issue:
- getattr(os, "rem" + "ove")
- __import__("os").remove
- exec("os.remove('…')") / eval("os.remove('…')")
- from os import *; remove('…')
- importlib.import_module("os").remove
"""

from __future__ import annotations

from agentos.tools.builtin.code_exec import (
    _check_code_destructive,
    _normalize_code,
)


class TestCheckCodeDestructiveLayer1:
    """Original regex patterns must still catch direct access."""

    def test_direct_os_remove(self):
        assert _check_code_destructive("os.remove('/tmp/x')") is not None

    def test_direct_shutil_rmtree(self):
        assert _check_code_destructive("shutil.rmtree('/tmp')") is not None

    def test_direct_path_unlink(self):
        assert _check_code_destructive("Path('/tmp/x').unlink()") is not None

    def test_os_system_with_rm(self):
        assert _check_code_destructive("os.system('rm -rf /')") is not None

    def test_subprocess_rm(self):
        assert _check_code_destructive("subprocess.run(['rm', '-rf', '/tmp'])") is not None

    def test_harmless_code_is_not_flagged(self):
        assert _check_code_destructive("print('hello world')") is None

    def test_string_literal_not_flagged(self):
        assert _check_code_destructive("x = 'os.remove is dangerous'") is None


class TestCodeNormalizer:
    """AST normalizer resolves obfuscated patterns."""

    def test_getattr_resolved(self):
        """getattr(os, 'remove') → os.remove"""
        normalized = _normalize_code("getattr(os, 'remove')('/tmp/x')")
        assert "os.remove" in normalized

    def test_getattr_concat_resolved(self):
        """getattr(os, 'rem' + 'ove') → os.remove"""
        normalized = _normalize_code("getattr(os, 'rem' + 'ove')('/tmp/x')")
        assert "os.remove" in normalized

    def test_dunder_import_resolved(self):
        """__import__('os').remove → os.remove"""
        normalized = _normalize_code("__import__('os').remove('/tmp/x')")
        assert "os.remove" in normalized

    def test_importlib_import_module_resolved(self):
        """importlib.import_module('os').remove → os.remove"""
        normalized = _normalize_code("importlib.import_module('os').remove('/tmp/x')")
        assert "os.remove" in normalized

    def test_importlib_dunder_import_resolved(self):
        """importlib.__import__('os').remove → os.remove"""
        normalized = _normalize_code("importlib.__import__('os').remove('/tmp/x')")
        assert "os.remove" in normalized

    def test_from_os_wildcard_sentinel(self):
        """from os import * should add a recognizable sentinel."""
        normalized = _normalize_code("from os import *\nremove('/tmp/x')")
        assert "os.remove" in normalized
        assert "/sentinel/from_wildcard" in normalized

    def test_exec_nested_normalized(self):
        """exec('os.remove(\\'...\\')') should normalize inner code."""
        normalized = _normalize_code("exec('os.remove(\\\"/tmp/x\\\")')")
        assert "os.remove" in normalized

    def test_eval_nested_normalized(self):
        """eval('os.remove(\\'...\\')') should normalize inner code."""
        normalized = _normalize_code("eval('os.remove(\\\"/tmp/x\\\")')")
        assert "os.remove" in normalized

    def test_unparseable_falls_through(self):
        """Code that can't be parsed should return unchanged."""
        raw = "x = '''\n  "
        assert _normalize_code(raw) == raw

    def test_harmless_code_passes_through(self):
        normalized = _normalize_code("print(sum([1, 2, 3]))")
        assert "remove" not in normalized


class TestCheckCodeDestructiveLayer2:
    """Layer 2 normalizer catches obfuscated bypasses."""

    def test_getattr_bypass(self):
        msg = _check_code_destructive("getattr(os, 'remove')('/tmp/x')")
        assert msg is not None
        assert "resolved" in msg

    def test_getattr_concat_bypass(self):
        msg = _check_code_destructive("getattr(os, 'rem' + 'ove')('/tmp/x')")
        assert msg is not None
        assert "resolved" in msg

    def test_dunder_import_bypass(self):
        msg = _check_code_destructive("__import__('os').remove('/tmp/x')")
        assert msg is not None
        assert "resolved" in msg

    def test_importlib_import_module_bypass(self):
        msg = _check_code_destructive("importlib.import_module('os').remove('/tmp/x')")
        assert msg is not None
        assert "resolved" in msg

    def test_from_os_import_star_bypass(self):
        msg = _check_code_destructive("from os import *\nremove('/tmp/x')")
        assert msg is not None

    def test_exec_wrapped_destructive(self):
        msg = _check_code_destructive("exec(\"os.remove('/tmp/x')\")")
        assert msg is not None

    def test_eval_wrapped_destructive(self):
        msg = _check_code_destructive("eval(\"os.remove('/tmp/x')\")")
        assert msg is not None

    def test_recursive_exec_eval(self):
        msg = _check_code_destructive("exec(\"eval('os.remove(\\\"/tmp/x\\\")')\")")
        assert msg is not None




class TestFalsePositives:
    """Code that looks destructive but isn't must stay clean."""

    def test_list_remove_not_flagged(self):
        assert _check_code_destructive("[1, 2, 3].remove(2)") is None

    def test_dict_pop_not_flagged(self):
        assert _check_code_destructive("d.pop('key')") is None

    def test_comment_only_not_flagged(self):
        assert _check_code_destructive("# os.remove is dangerous") is None

    def test_string_containing_os_remove(self):
        assert _check_code_destructive("x = 'os.remove is a function'") is None

    def test_import_path_not_flagged(self):
        assert _check_code_destructive("import os.path") is None

    @staticmethod
    def _has_os_remove_in_normalized(code):
        normalized = _normalize_code(code)
        # Check if the normalizer spuriously introduces os.remove
        return "os.remove" in normalized

    def test_getattr_builtins_normalized_correctly(self):
        """getattr on something other than a destructive module should not
        create a false os.remove pattern."""
        norm = _normalize_code("getattr(obj, 'method')")
        assert "os.remove" not in norm
