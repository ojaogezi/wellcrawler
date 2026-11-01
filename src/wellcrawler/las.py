"""Minimal, tolerant LAS 1.2 / 2.0 parser (stdlib only).

Supports the common case: WRAP=NO, whitespace-delimited ``~A`` data, a
``~W`` well header and ``~C`` curve definitions.  Anything structural we
cannot represent (WRAP=YES, no ``~A``) raises :class:`LasError` with a
reason; the crawler records the failure instead of aborting.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


class LasError(ValueError):
    """Raised when a file cannot be parsed as a usable LAS."""


@dataclass
class Curve:
    mnemonic: str
    unit: str = ""
    description: str = ""


@dataclass
class LasFile:
    well: Optional[str] = None
    uwi: Optional[str] = None
    field_name: Optional[str] = None  # not `field`: that name is taken by dataclasses
    null: Optional[float] = None
    params: dict = field(default_factory=dict)  # ~W entries, mnemonic -> value
    curves: list = field(default_factory=list)  # list[Curve]
    columns: dict = field(default_factory=dict)  # mnemonic -> list[float|None]
    warnings: list = field(default_factory=list)

    def samples(self, mnemonic: str):
        """Return (depths, values) for a curve, dropping null pairs."""
        depth_mn = self.depth_mnemonic()
        if depth_mn is None or mnemonic not in self.columns:
            return [], []
        pairs = [
            (d, v)
            for d, v in zip(self.columns[depth_mn], self.columns[mnemonic])
            if d is not None and v is not None
        ]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def depth_mnemonic(self) -> Optional[str]:
        lowered = {m.lower(): m for m in self.columns}
        for candidate in ("dept", "depth", "md", "dep"):
            if candidate in lowered:
                return lowered[candidate]
        return next(iter(self.columns), None)


def parse(text: str) -> LasFile:
    las = LasFile()
    section = ""
    seen_sections = set()
    mnemonics: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("~"):
            section = line[1].upper() if len(line) > 1 else ""
            seen_sections.add(section)
            if section == "A":
                mnemonics = [c.mnemonic for c in las.curves]
                for m in mnemonics:
                    las.columns[m] = []
            continue

        if section == "A":
            _parse_data_line(line, las, mnemonics)
        elif section == "V":
            mnemonic, unit, value = _parse_header_line(line, value_expected=True)
            if mnemonic:
                las.params[mnemonic.upper()] = value
                if mnemonic.upper() == "WRAP" and value.upper().startswith("Y"):
                    raise LasError("WRAP=YES (wrapped) LAS files are not supported")
        elif section == "W":
            mnemonic, unit, value = _parse_header_line(line, value_expected=True)
            if not mnemonic:
                continue
            key = mnemonic.upper()
            las.params[key] = value
            if key == "WELL" and value:
                las.well = value
            elif key in ("UWI", "API") and value:
                las.uwi = value
            elif key == "FLD" and value:
                las.field_name = value
            elif key == "NULL" and value:
                try:
                    las.null = float(value)
                except ValueError:
                    pass
        elif section == "C":
            mnemonic, unit, value = _parse_header_line(line, value_expected=False)
            if not mnemonic:
                continue
            if mnemonic in las.columns or any(c.mnemonic == mnemonic for c in las.curves):
                suffix = 2
                while any(c.mnemonic == f"{mnemonic}_{suffix}" for c in las.curves):
                    suffix += 1
                las.warnings.append(f"duplicate curve mnemonic {mnemonic!r} renamed {mnemonic}_{suffix}")
                mnemonic = f"{mnemonic}_{suffix}"
            las.curves.append(Curve(mnemonic, unit, value))
        elif section == "P":
            mnemonic, unit, value = _parse_header_line(line, value_expected=True)
            if mnemonic:
                las.params.setdefault("P_" + mnemonic.upper(), value)

    if "A" not in seen_sections:
        raise LasError("no ~A data section found")
    if not las.curves:
        raise LasError("no ~C curve definitions found")
    return las


def _parse_header_line(line: str, value_expected: bool):
    """Parse ``MNEM.UNIT VALUE : COMMENT`` for header sections.

    ``~C`` lines carry only a unit; ``~W``/``~V``/``~P`` lines carry unit
    then value, and a single trailing token is treated as the value (many
    real files write ``WELL. AB-12-34`` with an empty unit).
    """
    head, _, _comment = line.partition(":")
    head = head.strip()
    mnemonic, _, rest = head.partition(".")
    mnemonic = mnemonic.strip()
    rest = rest.strip()
    if not mnemonic:
        return "", "", ""
    if rest.startswith("[") and "]" in rest:
        end = rest.index("]")
        unit = rest[1:end].strip()
        value = rest[end + 1 :].strip()
        return mnemonic, unit, value
    tokens = rest.split()
    if not value_expected or not tokens:
        return mnemonic, " ".join(tokens), ""
    if len(tokens) == 1:
        return mnemonic, "", tokens[0]
    return mnemonic, tokens[0], " ".join(tokens[1:])


def _parse_data_line(line: str, las: LasFile, mnemonics: list[str]) -> None:
    tokens = line.split()
    if not mnemonics:
        return
    if len(tokens) < len(mnemonics):
        tokens = tokens + ["NaN"] * (len(mnemonics) - len(tokens))
    elif len(tokens) != len(mnemonics):
        las.warnings.append(f"dropping misaligned data line: {line[:60]}")
        return
    for mnemonic, token in zip(mnemonics, tokens):
        las.columns[mnemonic].append(_to_float(token, las.null))


def _to_float(token: str, null: Optional[float]) -> Optional[float]:
    try:
        value = float(token)
    except ValueError:
        return None
    if math.isnan(value):
        return None
    if null is not None and math.isclose(value, null, rel_tol=0.0, abs_tol=1e-9):
        return None
    return value
