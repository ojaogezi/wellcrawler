"""Well identity: normalization, alias merging and display-name choice.

Everything a file can tell us about a well name (LAS ``~W`` header, report
title line, filename tokens, a UWI anywhere in the name) is folded into a
single canonical :class:`Registry`.  Two spellings merge into one well when
their normalized forms match, or when they share a UWI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# UWI-style identifiers, e.g. 100/12-34-056-25W4/0 or 00/14-22-056-25W4/0
UWI_RE = re.compile(r"\d{1,3}/\d{2}-\d{2}-\d{3}-\d{2}[A-Z]\d(?:/[A-Z]?\d)?")
# Loose pattern without the leading pool number, e.g. 12-34-056-25W4
LSID_RE = re.compile(r"\b\d{2}-\d{2}-\d{3}-\d{2}[A-Z]\d\b")

_NOISE_TOKENS = {
    "final", "final2", "finalfinal", "copy", "export", "exports", "new", "old",
    "v1", "v2", "v3", "v4", "las", "log", "logs", "lwd", "checkshot", "chk",
    "core", "cores", "desc", "description", "report", "reports", "data",
    "digital", "scan", "scans", "scanned", "temp", "tmp", "edit", "edited",
    "updated", "clean", "cleanup", "fixed", "compressed", "small", "test",
    "draft", "backup", "bak", "raw", "processed", "interpretation", "for",
    "the", "and", "with", "from", "use", "do", "not", "delete", "moved",
    "tops", "shows", "section", "xsec", "curve", "curves", "survey", "surveys",
    "legacy", "composite", "original", "descriptions",
}

# Version-ish junk glued onto filenames: vFINAL, run1, RevB, 2, f, ...
_JUNK_RE = re.compile(
    r"^(?:v\d+|(?:v)?final\w*|run\d+|rev\w*|ver\w*|copy\w*|\d{1,2}|[a-z])$", re.IGNORECASE
)


def _is_noise(token: str) -> bool:
    return token.lower() in _NOISE_TOKENS or bool(_JUNK_RE.fullmatch(token))


def normalize(name: str) -> str:
    """Fold a well name to its canonical merge key."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def find_uwi(text: str) -> Optional[str]:
    if not text:
        return None
    match = UWI_RE.search(text.upper().replace(" ", ""))
    if match:
        return match.group(0)
    match = LSID_RE.search(text.upper())
    return match.group(0) if match else None


def canonical_uwi(uwi: str) -> str:
    """Reduce full UWIs (100/14-22-056-25W4/0) to their merge key (14-22-056-25W4)."""
    match = LSID_RE.search(uwi.upper())
    return match.group(0) if match else uwi.upper()


def name_from_filename(path: str) -> str:
    """Guess a well name from a messy filename by dropping noise tokens.

    Never returns None: a file whose name is pure noise still belongs to
    *some* well, and naming the well after the file keeps the catalog
    honest while flagging it for manual review.
    """
    stem = re.sub(r"\.[^.]*$", "", path.replace("\\", "/").split("/")[-1])
    # Split on separators that never appear inside a well name; hyphens do
    # (AB-12-34, 14-22-056-25W4), so they are kept inside tokens.
    tokens = re.split(r"[\s_.()]+", stem)
    kept = [t for t in tokens if t and not _is_noise(t)]
    # A UWI spotted in the filename is the strongest possible signal.
    uwi = find_uwi(stem.replace("_", "/"))
    if uwi:
        return uwi
    if kept:
        return " ".join(kept)
    return stem or "unknown-well"


@dataclass
class Well:
    well_key: str
    display_name: str
    uwi: Optional[str] = None
    aliases: set = field(default_factory=set)
    _display_votes: dict = field(default_factory=dict)
    _best_priority: dict = field(default_factory=dict)

    def vote_display(self, candidate: str, priority: int) -> None:
        if not candidate:
            return
        self.aliases.add(candidate)
        self._display_votes[candidate] = self._display_votes.get(candidate, 0) + priority
        self._best_priority[candidate] = max(self._best_priority.get(candidate, 0), priority)

    def finalize_display(self) -> None:
        if not self._display_votes:
            return
        # Most trustworthy source wins first; then total votes, then brevity.
        ranked = sorted(
            self._display_votes.items(),
            key=lambda kv: (-self._best_priority[kv[0]], -kv[1], len(kv[0]), kv[0]),
        )
        self.display_name = ranked[0][0]


class Registry:
    """Collects well-name variants and merges them into canonical wells."""

    # Display-name priorities: LAS headers are most trustworthy, then report
    # title lines, then tops/core tables, then filename guesses.
    PRIORITY_LAS = 8
    PRIORITY_REPORT = 4
    PRIORITY_TABLE = 2
    PRIORITY_FILENAME = 1

    def __init__(self) -> None:
        self._by_key: dict[str, Well] = {}
        self._by_uwi: dict[str, str] = {}

    def register(
        self,
        names: list,
        uwi: Optional[str] = None,
        priority: int = PRIORITY_FILENAME,
    ) -> Well:
        """Register name variants (plus optional UWI) and return the merged well."""
        cleaned = [n.strip() for n in names if n and n.strip()]
        uwi = uwi or next((find_uwi(n) for n in cleaned if find_uwi(n)), None)

        keys = {normalize(n) for n in cleaned}
        keys.discard("")
        uwi_key = canonical_uwi(uwi) if uwi else None
        well_key: Optional[str] = None
        for key in keys:
            if key in self._by_key:
                well_key = key
                break
        if well_key is None and uwi_key and uwi_key in self._by_uwi:
            well_key = self._by_uwi[uwi_key]
        if well_key is None:
            well_key = sorted(keys)[0] if keys else normalize(uwi_key or "UNKNOWN")

        well = self._by_key.get(well_key)
        if well is None:
            well = Well(well_key=well_key, display_name="")
            self._by_key[well_key] = well
        for key in keys:
            if key not in self._by_key:
                self._by_key[key] = well
        if uwi and not well.uwi:
            well.uwi = uwi
        if uwi_key:
            self._by_uwi.setdefault(uwi_key, well.well_key)
        for name in cleaned:
            well.vote_display(name, priority)
        return well

    def wells(self) -> list:
        unique = {}
        for well in self._by_key.values():
            unique.setdefault(well.well_key, well)
        return sorted(unique.values(), key=lambda w: w.well_key)
