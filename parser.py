"""Cobertura XML parser.

Standalone module: no Hermes imports here. Only the standard library.

A future `coverage json` parser would be an additive function in this module,
not a refactor.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union


@dataclass(frozen=True)
class ModuleCoverage:
    path: str
    package: str
    lines_total: int
    lines_covered: int
    pct: float


# Hardening bounds. Cobertura reports are typically a few MB; field
# values are short identifiers. Anything beyond these limits is either
# pathological or hostile — reject before the data enters the DB and
# can flow back to the LLM as context.
_MAX_REPORT_BYTES = 50 * 1024 * 1024
_MAX_FIELD_LEN = 512


def _normalize_path(filename: str) -> str:
    """Coalesce producer-specific path forms so the same module lands under
    one `path` value across snapshots.

    - Backslash → forward slash (Windows producers).
    - Collapse repeated separators (e.g. an escaped `\\\\` becoming `//`).
    - Strip a leading `./` repeatedly.
    - Strip any leading slashes (treat absolute paths as repo-relative).
    """
    p = filename.replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    while p.startswith("./"):
        p = p[2:]
    while p.startswith("/"):
        p = p[1:]
    return p


def _is_safe_field(value: str) -> bool:
    """True iff `value` is safe to persist and later echo back to the LLM.

    Cobertura `filename` and `package` attributes flow verbatim into the
    DB and from there into LLM tool responses and the `pre_llm_call`
    summary. A malicious report can shape that downstream content if it
    smuggles in control characters (notably newlines, escaping the
    expected single-line shape), oversized payloads (instruction-shaped
    prose), or `..` traversal segments (confusing any downstream consumer
    that re-uses the path as a filesystem lookup).
    """
    if not isinstance(value, str):
        return False
    if len(value) > _MAX_FIELD_LEN:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        return False
    parts = value.replace("\\", "/").split("/")
    return ".." not in parts


def _reject_dtd(raw: bytes, p: Path) -> None:
    """Cobertura reports never legitimately carry a DOCTYPE or internal
    ENTITY declaration. Reject them up-front to block billion-laughs and
    quadratic-blowup expansion attacks: `xml.etree.ElementTree` expands
    internal entities without any size cap.
    """
    # DTDs must precede the root element; a small head slice catches them
    # without scanning the whole file twice.
    head = raw[: 64 * 1024].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ValueError(
            f"coverage report contains a DOCTYPE or ENTITY declaration; "
            f"rejected to prevent XML entity-expansion attacks: {p}"
        )


def _count_covered_lines(lines_el) -> tuple[int, int]:
    """Return (total, covered) line counts for a Cobertura `<lines>` element."""
    if lines_el is None:
        return 0, 0
    lines = lines_el.findall("line")
    covered = 0
    for line in lines:
        try:
            if int(line.get("hits", "0")) > 0:
                covered += 1
        except (TypeError, ValueError):
            # Unparseable `hits` is treated as 0 — Cobertura producers should
            # always emit integers, but be defensive.
            continue
    return len(lines), covered


def _module_from_class(class_el, package_name: str) -> Optional[ModuleCoverage]:
    """Convert a Cobertura `<class>` element to a `ModuleCoverage`, or None
    if the element lacks a `filename` attribute or carries an unsafe field
    value (see `_is_safe_field`)."""
    filename = class_el.get("filename")
    if not filename:
        return None
    if not _is_safe_field(filename) or not _is_safe_field(package_name):
        return None
    total, covered = _count_covered_lines(class_el.find("lines"))
    pct = (100.0 * covered / total) if total > 0 else 0.0
    return ModuleCoverage(
        path=_normalize_path(filename),
        package=package_name,
        lines_total=total,
        lines_covered=covered,
        pct=pct,
    )


def parse_report(path: Union[str, Path]) -> List[ModuleCoverage]:
    """Parse a Cobertura XML report into a list of `ModuleCoverage` rows.

    Raises `ValueError` on a missing, malformed, oversized, structurally
    empty, or DTD-carrying report.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ValueError(f"coverage report not found: {p}")

    size = p.stat().st_size
    if size > _MAX_REPORT_BYTES:
        raise ValueError(
            f"coverage report exceeds {_MAX_REPORT_BYTES} byte limit "
            f"({size} bytes): {p}"
        )

    raw = p.read_bytes()
    _reject_dtd(raw, p)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"malformed Cobertura XML in {p}: {e}") from e

    if root.tag != "coverage":
        raise ValueError(
            f"expected root <coverage>, got <{root.tag}> in {p}"
        )

    packages_el = root.find("packages")
    if packages_el is None:
        raise ValueError(f"no <packages> element in {p}")

    rows: List[ModuleCoverage] = []
    for package_el in packages_el.findall("package"):
        package_name = package_el.get("name", "") or ""
        classes_el = package_el.find("classes")
        if classes_el is None:
            continue
        for class_el in classes_el.findall("class"):
            module = _module_from_class(class_el, package_name)
            if module is not None:
                rows.append(module)

    if not rows:
        raise ValueError(f"no <class> entries found in {p}")
    return rows
