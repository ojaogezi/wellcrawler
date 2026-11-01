"""Content-based file classification for messy subsurface archives.

Files in a folder like ``temp(14)`` are misnamed by definition, so the
classifier trusts content over extension: it sniffs the first chunk of the
file and decides what it is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

SNIFF_BYTES = 65536

# Kind constants (kept as plain strings so they serialize straight into SQLite).
LAS = "las"
CHECKSHOT = "checkshot"
CORE_DESC = "core_desc"
TOPS_CSV = "tops_csv"
REPORT = "report"
UNKNOWN = "unknown"

_DATA_KINDS = {LAS, CHECKSHOT, CORE_DESC, TOPS_CSV, REPORT}


@dataclass
class Classification:
    kind: str
    confidence: float
    reason: str

    @property
    def is_data(self) -> bool:
        return self.kind in _DATA_KINDS


def _head(path, n=SNIFF_BYTES) -> str:
    with open(path, "rb") as fh:
        return fh.read(n).decode("utf-8", errors="replace")


def classify(path) -> Classification:
    """Classify a file by sniffing its content.

    The order matters: LAS and paginated reports are matched structurally
    first; anything CSV-shaped is scored against known header vocabularies.
    """
    try:
        text = _head(path)
    except OSError as exc:
        return Classification(UNKNOWN, 0.0, f"unreadable: {exc}")

    if not text.strip():
        return Classification(UNKNOWN, 0.0, "empty file")

    if _looks_like_las(text):
        return Classification(LAS, 0.95, "LAS section markers (~V/~W/~C/~A) found")

    csv_like = _csv_score(text)
    if csv_like is not None:
        headers = csv_like
        kind, confidence, reason = _score_csv_headers(headers)
        if kind is not UNKNOWN:
            return Classification(kind, confidence, reason)
        return Classification(UNKNOWN, 0.3, f"CSV-like but headers unrecognized: {headers[:6]}")

    if _looks_like_report(text):
        return Classification(REPORT, 0.8, "paginated text report with depth-tied records")

    return Classification(UNKNOWN, 0.1, "no recognized structure")


def _looks_like_las(text: str) -> bool:
    for line in text.splitlines()[:200]:
        stripped = line.strip()
        if stripped.startswith("~") and len(stripped) > 1:
            section = stripped[1].upper()
            if section in "VWCAO":
                return True
        if stripped.upper().startswith("#LAS"):
            return True
    return False


_CSV_MIN_LINES = 2


def _csv_score(text: str) -> "Optional[list[str]]":
    """Return header fields if the text looks like CSV, else None."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:8]
    if len(lines) < _CSV_MIN_LINES:
        return None
    delim = _pick_delimiter(lines)
    if delim is None:
        return None
    headers = [h.strip().strip('"').lower() for h in lines[0].split(delim)]
    return headers if len(headers) >= 2 else None


def _pick_delimiter(lines: list[str]) -> Optional[str]:
    for delim in (",", ";", "\t"):
        hits = sum(1 for ln in lines if delim in ln)
        if hits >= max(2, int(0.6 * len(lines))):
            return delim
    return None


def _score_csv_headers(headers: list[str]):
    joined = " | ".join(headers)
    has_depth = _has_depth_column(headers)
    has_time = any(
        token in joined for token in ("twt", "two way", "two-way", "owt", "one way", "one-way", "time")
    )
    has_formation = any(
        token in joined for token in ("formation", "horizon", "marker", "surface", "pick")
    )
    has_core_terms = any(
        token in joined
        for token in ("litholog", "porosit", "permeab", "core", "descri")
    )
    has_interval = _has_interval_columns(headers)

    if has_depth and has_time:
        return CHECKSHOT, 0.9, "depth + time columns (checkshot)"
    if has_formation and has_depth:
        return TOPS_CSV, 0.9, "formation + depth columns (tops table)"
    if has_core_terms and has_interval:
        return CORE_DESC, 0.85, "interval + core description columns"
    if has_core_terms and has_depth:
        return CORE_DESC, 0.7, "depth + core description columns"
    return UNKNOWN, 0.0, ""


def _norm_header(h: str) -> str:
    return "".join(ch for ch in h.lower() if ch.isalnum())


def _has_depth_column(headers: list[str]) -> bool:
    for h in headers:
        n = _norm_header(h)
        if n in ("md", "depth", "depthmd", "measureddepth", "dept", "top", "topdepth", "depthfrom"):
            return True
        if "depth" in n or n == "md" or n.startswith("md"):
            return True
    return False


def _has_interval_columns(headers: list[str]) -> bool:
    norms = {_norm_header(h) for h in headers}
    tops = {"top", "topdepth", "depthfrom", "from", "depth_top", "top_md", "startdepth"}
    bases = {"base", "basedepth", "depthto", "to", "depth_base", "base_md", "enddepth"}
    return bool(norms & tops) and bool(norms & bases)


_SHOW_KEYWORDS = ("show", "fluorescen", "gas ", "oil ", "cut mud", "stain", "kick", "blow", "bitumen")
_REPORT_MIN_PAGES = 2


def _looks_like_report(text: str) -> bool:
    if text.count("\f") + 1 < _REPORT_MIN_PAGES and "page" not in text[:4000].lower():
        return False
    low = text.lower()
    keyword_hits = sum(1 for kw in _SHOW_KEYWORDS if kw in low)
    has_tops = (
        "top" in low
        or "formation" in low
        or re.search(r"\.{4,}\s*\d", text) is not None  # dotted-leader tops list
    )
    has_depthish = any(ch.isdigit() for ch in text)
    return keyword_hits >= 2 and has_tops and has_depthish
