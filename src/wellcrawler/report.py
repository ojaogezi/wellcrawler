"""Extract depth-tied records from paginated legacy text reports.

Reports are split on form-feed (``\\f``) page boundaries — the text-layer
equivalent of "source page".  Tops, hydrocarbon shows and mud-weight /
miscellaneous notes are recognized line by line; every record keeps the
page and line it came from, plus the verbatim line as an excerpt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .tables import Top

_INTERVAL = re.compile(
    r"(\d{2,5}(?:\.\d+)?)\s*(m|ft|mbgl|md|tvdss)?\s*[-\u2013]\s*"
    r"(\d{2,5}(?:\.\d+)?)\s*(m|ft|mbgl|md|tvdss)?",
    re.IGNORECASE,
)
_SINGLE_DEPTH = re.compile(r"(\d{2,5}(?:\.\d+)?)\s*(m\b|ft\b|mbgl|md\b|tvdss)?", re.IGNORECASE)


def _plausible(number: str, unit: "str | None") -> bool:
    """Reject stray small integers (page numbers, counts) as depths."""
    if unit:
        return True
    return "." in number or float(number) >= 100.0

_DOTTED_TOP = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 ()\-.'/]*?[A-Za-z0-9)])"  # formation name
    r"[\s.]*\.{2,}[\s.]*"                                  # dot leader
    r"(\d+(?:\.\d+)?)\s*(m|ft|mbgl|kb)?\s*$"
)
_TOP_PREFIXED = re.compile(
    r"^\s*(?:top of|top)\s+(.+?)\s*[:@]\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)

_GAS = re.compile(r"\b(gas|gascut|gas cut|methane|kick|blow(?:\s*out)?|connection gas)\b", re.IGNORECASE)
_OIL = re.compile(r"\b(oil|oilstain|oil stain|bitumen|dead oil|live oil|oil show)\b", re.IGNORECASE)
_FLUOR = re.compile(r"\b(fluorescen\w*|fluorescing|cut|stain|show|odor|bleed)\b", re.IGNORECASE)
_SHOW_HINT = re.compile(
    r"\b(show|fluorescen\w*|gas|oil|cut mud|stain|kick|blow|bitumen|odor|bleed)\b", re.IGNORECASE
)
_MUD_WEIGHT = re.compile(
    r"\bmud\s*weight\b|\bm\.?w\.?\b", re.IGNORECASE
)
_MW_VALUE = re.compile(r"(\d{3,4}(?:\.\d+)?)\s*(kg/m3|kg/m\u00b3|ppg|g/cm3|g/cc|SG)", re.IGNORECASE)
_WELL_LINE = re.compile(r"^\s*WELL\s*(?:NAME|#|:|\s)\s*([A-Za-z0-9][A-Za-z0-9\-/ ]+?)\s*$|^\s*WELL\s*[:#]\s*(\S.*)$", re.IGNORECASE)


@dataclass
class Show:
    top_md: float
    base_md: float
    fluid: str
    description: str
    page: int
    line_no: int
    excerpt: str


@dataclass
class Observation:
    kind: str
    depth_md: Optional[float]
    text: str
    page: int
    line_no: int
    excerpt: str


@dataclass
class ReportResult:
    well_hint: Optional[str] = None
    tops: list = field(default_factory=list)   # list[Top]
    shows: list = field(default_factory=list)  # list[Show]
    observations: list = field(default_factory=list)


def parse_report(text: str) -> ReportResult:
    result = ReportResult()
    global_line = 0
    for page_no, page in enumerate(text.split("\f"), start=1):
        for line in page.splitlines():
            global_line += 1
            stripped = line.strip()
            if not stripped:
                continue
            if result.well_hint is None:
                match = _WELL_LINE.match(stripped)
                if match:
                    result.well_hint = (match.group(1) or match.group(2) or "").strip()
            _maybe_top(result, stripped, page_no, global_line)
            _maybe_show(result, stripped, page_no, global_line)
            _maybe_observation(result, stripped, page_no, global_line)
    return result


def _maybe_top(result: ReportResult, line: str, page: int, line_no: int) -> None:
    match = _DOTTED_TOP.match(line) or _TOP_PREFIXED.match(line)
    if not match:
        return
    groups = match.groups()
    formation, depth = groups[0].strip(), float(groups[1])
    unit = (groups[2] or "m") if len(groups) > 2 else "m"
    if unit.lower().startswith("ft"):
        unit = "ft"
    else:
        unit = "m"
    # Avoid re-capturing show lines that happen to look dotted.
    if _SHOW_HINT.search(line):
        return
    result.tops.append(
        Top(formation=formation, depth_md=depth, unit=unit, line_no=line_no,
            page=page, excerpt=line)
    )


def _maybe_show(result: ReportResult, line: str, page: int, line_no: int) -> None:
    if not _SHOW_HINT.search(line):
        return
    interval = _INTERVAL.search(line)
    top = base = None
    if interval and all(
        _plausible(interval.group(i), interval.group(i + 1)) for i in (1, 3)
    ):
        top, base = float(interval.group(1)), float(interval.group(3))
    else:
        for match in _SINGLE_DEPTH.finditer(line):
            if _plausible(match.group(1), match.group(2)):
                top = base = float(match.group(1))
                break
    if top is None:
        return
    if top > base:
        top, base = base, top
    fluid = "gas" if _GAS.search(line) else "oil" if _OIL.search(line) else "fluorescence" if _FLUOR.search(line) else "show"
    result.shows.append(
        Show(
            top_md=top,
            base_md=base,
            fluid=fluid,
            description=line,
            page=page,
            line_no=line_no,
            excerpt=line,
        )
    )


def _depth_in_line(line: str):
    """First plausible depth in the line, preferring numbers tagged with a unit."""
    with_unit = without_unit = None
    for match in _SINGLE_DEPTH.finditer(line):
        number, unit = match.group(1), match.group(2)
        if not _plausible(number, unit):
            continue
        if unit and with_unit is None:
            with_unit = float(number)
        elif not unit and without_unit is None:
            without_unit = float(number)
    return with_unit if with_unit is not None else without_unit


def _maybe_observation(result: ReportResult, line: str, page: int, line_no: int) -> None:
    if _MUD_WEIGHT.search(line):
        mw = _MW_VALUE.search(line)
        depth = _depth_in_line(line)
        text = f"mud weight {mw.group(0)}" if mw else line
        result.observations.append(
            Observation("mud_weight", depth, text, page, line_no, line)
        )
