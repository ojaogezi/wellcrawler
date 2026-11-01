"""Tolerant parsers for CSV-shaped subsurface tables.

All three share a fuzzy-header mapping strategy: normalize header names,
match against known vocabularies, and raise :class:`TableError` naming the
missing column when a required field cannot be found.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Optional


class TableError(ValueError):
    """Raised when a required column cannot be found."""


def _norm(h: str) -> str:
    return "".join(ch for ch in h.lower() if ch.isalnum())


def _read_rows(text: str):
    """Parse CSV-ish text into normalized-key dicts (delimiter by simple count)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    counts = {d: sum(ln.count(d) for ln in lines[:6]) for d in (",", ";", "\t")}
    delim = max(counts, key=counts.get)
    if counts[delim] == 0:
        raise TableError("no delimiter found (expected a CSV-like table)")
    rows_raw = [
        row for row in csv.reader(io.StringIO(text), delimiter=delim)
        if row and any(cell.strip() for cell in row)
    ]
    if len(rows_raw) < 2:
        return []
    header = [_norm(h) for h in rows_raw[0]]
    return [dict(zip(header, (c.strip() for c in raw))) for raw in rows_raw[1:]]


def _find(rows, *candidates, optional=False, context="") -> Optional[str]:
    """Find a normalized key present in the rows' keyspace, fuzzy."""
    if not rows:
        if optional:
            return None
        raise TableError(f"{context}: file has no data rows")
    keys = rows[0].keys()
    for candidate in candidates:
        if candidate in keys:
            return candidate
    for key in keys:  # substring fallback
        for candidate in candidates:
            if candidate in key:
                return key
    if optional:
        return None
    raise TableError(f"{context}: no column matching {'/'.join(candidates)}")


def _float(value, context):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        raise TableError(f"{context}: non-numeric value {value!r}")


# ---------------------------------------------------------------- checkshots


@dataclass
class Checkshot:
    points: list = field(default_factory=list)  # (md, tvd, twt) tuples
    warnings: list = field(default_factory=list)


def parse_checkshot(text: str) -> Checkshot:
    rows = _read_rows(text)
    md_col = _find(rows, "md", "depth", "measureddepth", context="checkshot")
    tvd_col = _find(rows, "tvd", optional=True, context="checkshot")
    twt_col = _find(rows, "twt", "twowaytime", "twoway", "time", "owt", "onewaytime", context="checkshot")
    points = []
    for i, row in enumerate(rows, start=2):
        md = _float(row.get(md_col), f"checkshot row {i}")
        tvd = _float(row.get(tvd_col), f"checkshot row {i}") if tvd_col else None
        twt = _float(row.get(twt_col), f"checkshot row {i}")
        if md is None or twt is None:
            continue
        points.append((md, tvd if tvd is not None else md, twt))
    if not points:
        raise TableError("checkshot: no usable (depth, time) rows")
    return Checkshot(points=points)


# ------------------------------------------------------------- core tables


@dataclass
class CoreInterval:
    top_md: float
    base_md: float
    lithology: str
    porosity: Optional[float]
    permeability: Optional[float]
    description: str
    line_no: Optional[int] = None


def parse_core_desc(text: str) -> list:
    rows = _read_rows(text)
    top_col = _find(rows, "top", "topdepth", "depthfrom", "depthtop", "topmd", "from", context="core")
    base_col = _find(rows, "base", "basedepth", "depthto", "depthbase", "basemd", "to", context="core")
    lith_col = _find(rows, "lithology", "lith", "rocktype", optional=True, context="core")
    por_col = _find(rows, "porosity", "por", "phis", optional=True, context="core")
    perm_col = _find(rows, "permeability", "perm", "kdyn", optional=True, context="core")
    desc_col = _find(rows, "description", "desc", "comments", "remark", optional=True, context="core")

    intervals = []
    for i, row in enumerate(rows, start=2):
        top = _float(row.get(top_col), f"core row {i}")
        base = _float(row.get(base_col), f"core row {i}")
        if top is None or base is None:
            continue
        intervals.append(
            CoreInterval(
                top_md=top,
                base_md=max(base, top),
                lithology=(row.get(lith_col) or "").strip() if lith_col else "",
                porosity=_float(row.get(por_col), f"core row {i}") if por_col else None,
                permeability=_float(row.get(perm_col), f"core row {i}") if perm_col else None,
                description=(row.get(desc_col) or "").strip() if desc_col else "",
                line_no=i,
            )
        )
    if not intervals:
        raise TableError("core: no usable interval rows")
    return intervals


# --------------------------------------------------------------- tops tables


@dataclass
class Top:
    formation: str
    depth_md: float
    unit: str = "m"
    line_no: Optional[int] = None
    page: Optional[int] = None
    excerpt: Optional[str] = None


def parse_tops_csv(text: str) -> list:
    rows = _read_rows(text)
    fm_col = _find(rows, "formation", "fm", "horizon", "marker", "surface", "pick", "zone", context="tops")
    depth_col = _find(rows, "depth", "md", "topdepth", "depthmd", "top", context="tops")
    unit_col = _find(rows, "unit", "units", optional=True, context="tops")

    tops = []
    for i, row in enumerate(rows, start=2):
        name = (row.get(fm_col) or "").strip()
        depth = _float(row.get(depth_col), f"tops row {i}")
        if not name or depth is None:
            continue
        tops.append(
            Top(
                formation=name,
                depth_md=depth,
                unit=(row.get(unit_col) or "m").strip() or "m" if unit_col else "m",
                line_no=i,
            )
        )
    if not tops:
        raise TableError("tops: no usable rows")
    return tops
