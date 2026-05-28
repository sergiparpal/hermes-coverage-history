"""Phase 1: Cobertura parser tests."""

import pytest

import parser as cov_parser


def test_parse_fixture_returns_three_modules(sample_cobertura_xml):
    rows = cov_parser.parse_report(sample_cobertura_xml)
    assert len(rows) == 3


def test_parse_fixture_correct_counts(sample_cobertura_xml):
    by_path = {r.path: r for r in cov_parser.parse_report(sample_cobertura_xml)}

    foo = by_path["pkg_a/foo.py"]
    assert foo.lines_total == 4
    assert foo.lines_covered == 3
    assert foo.pct == 75.0
    assert foo.package == "pkg_a"

    bar = by_path["pkg_a/bar.py"]
    assert bar.lines_total == 2
    assert bar.lines_covered == 2
    assert bar.pct == 100.0
    assert bar.package == "pkg_a"

    baz = by_path["pkg_b/baz.py"]
    assert baz.lines_total == 4
    assert baz.lines_covered == 1
    assert baz.pct == 25.0
    assert baz.package == "pkg_b"


def test_parse_missing_file_raises(tmp_path):
    with pytest.raises(ValueError):
        cov_parser.parse_report(tmp_path / "does-not-exist.xml")


def test_parse_malformed_xml_raises(tmp_path):
    p = tmp_path / "broken.xml"
    p.write_text("<coverage><packages><package></coverage>")
    with pytest.raises(ValueError):
        cov_parser.parse_report(p)


def test_parse_wrong_root_raises(tmp_path):
    p = tmp_path / "wrong.xml"
    p.write_text('<?xml version="1.0"?><not-coverage/>')
    with pytest.raises(ValueError):
        cov_parser.parse_report(p)


def test_parse_no_classes_raises(tmp_path):
    p = tmp_path / "empty.xml"
    p.write_text(
        '<?xml version="1.0"?><coverage><packages></packages></coverage>'
    )
    with pytest.raises(ValueError):
        cov_parser.parse_report(p)


def test_parse_normalizes_paths(tmp_path):
    """L4: leading './', leading '/', and Windows backslashes must collapse
    so the same file lands under one `path` across snapshots."""
    p = tmp_path / "norm.xml"
    p.write_text(
        '<?xml version="1.0"?>'
        '<coverage><packages>'
        '<package name="pkg"><classes>'
        '<class name="a" filename="./pkg/a.py"><lines>'
        '<line number="1" hits="1"/></lines></class>'
        '<class name="b" filename="/abs/pkg/b.py"><lines>'
        '<line number="1" hits="1"/></lines></class>'
        '<class name="c" filename="pkg\\\\c.py"><lines>'
        '<line number="1" hits="1"/></lines></class>'
        '</classes></package>'
        '</packages></coverage>',
        encoding="utf-8",
    )
    paths = {r.path for r in cov_parser.parse_report(p)}
    assert paths == {"pkg/a.py", "abs/pkg/b.py", "pkg/c.py"}


def test_parse_handles_zero_line_class(tmp_path):
    """A `<class>` with no `<line>` children has pct 0.0 (guard against div0)."""
    p = tmp_path / "noline.xml"
    p.write_text(
        '<?xml version="1.0"?>'
        '<coverage><packages>'
        '<package name="pkg"><classes>'
        '<class name="x" filename="pkg/x.py"><lines/></class>'
        '</classes></package>'
        '</packages></coverage>'
    )
    rows = cov_parser.parse_report(p)
    assert len(rows) == 1
    assert rows[0].lines_total == 0
    assert rows[0].lines_covered == 0
    assert rows[0].pct == 0.0


# ---------- #4: format dispatch seam ---------------------------------------


def test_parse_report_explicit_cobertura(sample_cobertura_xml):
    """An explicit fmt='cobertura' is equivalent to the auto default."""
    rows = cov_parser.parse_report(sample_cobertura_xml, fmt="cobertura")
    assert len(rows) == 3


def test_parse_report_auto_resolves_to_cobertura(sample_cobertura_xml):
    auto = cov_parser.parse_report(sample_cobertura_xml)
    explicit = cov_parser.parse_report(sample_cobertura_xml, fmt="cobertura")
    assert [r.path for r in auto] == [r.path for r in explicit]


def test_parse_report_format_is_case_insensitive(sample_cobertura_xml):
    rows = cov_parser.parse_report(sample_cobertura_xml, fmt="Cobertura")
    assert len(rows) == 3


def test_parse_report_unknown_format_raises(sample_cobertura_xml):
    """An unsupported format is a clear ValueError, not a silent fallback."""
    with pytest.raises(ValueError) as exc:
        cov_parser.parse_report(sample_cobertura_xml, fmt="json")
    assert "format" in str(exc.value).lower()
