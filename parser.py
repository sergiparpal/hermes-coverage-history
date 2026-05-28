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
    if the element lacks a `filename` attribute."""
    filename = class_el.get("filename")
    if not filename:
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

    Raises `ValueError` on a missing, malformed, or structurally-empty report.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ValueError(f"coverage report not found: {p}")

    try:
        tree = ET.parse(str(p))
    except ET.ParseError as e:
        raise ValueError(f"malformed Cobertura XML in {p}: {e}") from e

    root = tree.getroot()
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
