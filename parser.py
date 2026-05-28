"""Cobertura XML parser.

Standalone module: no Hermes imports here. Only the standard library.

A future `coverage json` parser would be an additive function in this module,
not a refactor.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Union


@dataclass(frozen=True)
class ModuleCoverage:
    path: str
    package: str
    lines_total: int
    lines_covered: int
    pct: float


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
            filename = class_el.get("filename")
            if not filename:
                continue
            lines_el = class_el.find("lines")
            lines = list(lines_el.findall("line")) if lines_el is not None else []
            total = len(lines)
            covered = 0
            for line in lines:
                hits_attr = line.get("hits", "0")
                try:
                    if int(hits_attr) > 0:
                        covered += 1
                except (TypeError, ValueError):
                    # Treat unparseable `hits` as 0 — Cobertura producers should
                    # always emit integers, but be defensive.
                    continue
            pct = (100.0 * covered / total) if total > 0 else 0.0
            rows.append(
                ModuleCoverage(
                    path=filename,
                    package=package_name,
                    lines_total=total,
                    lines_covered=covered,
                    pct=pct,
                )
            )

    if not rows:
        raise ValueError(f"no <class> entries found in {p}")
    return rows
