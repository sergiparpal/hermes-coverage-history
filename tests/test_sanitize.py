"""#1: the shared output sanitizer used by tools.py and hook.py.

Consolidates the two byte-identical `_sanitize_for_*` helpers that previously
lived in tools.py (max_len=512) and hook.py (max_len=256).
"""

import sanitize


def test_strips_control_characters_and_del():
    assert sanitize.sanitize_text("a\nb\tc\x00d\x7fe") == "abcde"


def test_preserves_normal_path_text():
    assert sanitize.sanitize_text("pkg_a/foo.py") == "pkg_a/foo.py"


def test_default_max_len_is_512():
    assert len(sanitize.sanitize_text("x" * 1000)) == 512


def test_max_len_is_parameterized():
    # The hook passes max_len=256; tools relies on the 512 default.
    assert sanitize.sanitize_text("y" * 1000, max_len=256) == "y" * 256


def test_non_string_passes_through_unchanged():
    assert sanitize.sanitize_text(None) is None
    assert sanitize.sanitize_text(123) == 123
