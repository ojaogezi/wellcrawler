"""Plain-English queries over the catalog — deterministic, offline, no LLM.

The intent matchers below are deliberately simple pattern rules: they cover
the questions a geoscientist actually asks of a well archive ("which wells
have gas shows?", "where is the Basal Sand?", "what do we have for 12-34?")
and fall back to an honest help message instead of guessing.
"""
from __future__ import annotations

import re
from typing import Optional

from .catalog import Catalog


def answer(cat: Catalog, question: str) -> str:
    q = question.strip()
    low = q.lower()

    well_id = _find_well_token(cat, q)

    if re.search(r"\b(cross[- ]?section|xsec)\b", low):
        return _suggest_xsec(cat, q)
    if well_id and re.search(r"\b(data|files?|available)\b", low):
        return well_availability(cat, well_id)
    if re.search(r"\bshows?\b", low):
        if well_id:
            return well_shows(cat, well_id)
        return wells_with_shows(cat, fluid=_fluid_filter(low))
    if re.search(r"where is|where's|top of|depth of|how deep", low):
        formation = _formation_token(cat, q)
        if formation:
            return formation_tops(cat, formation, well_id)
    if re.search(r"\btops?\b", low):
        if well_id:
            return well_tops(cat, well_id)
        formation = _formation_token(cat, q)
        if formation:
            return formation_tops(cat, formation, None)
    if re.search(r"which|what|list|how many", low) and re.search(r"\bwells?\b", low):
        return list_wells(cat)

    return _fallback(cat)


# --------------------------------------------------------------- responders


def list_wells(cat: Catalog) -> str:
    rows = cat.wells()
    if not rows:
        return "No wells in the catalog. Run `wellcrawler crawl <folder>` first."
    lines = ["Wells in the archive:"]
    for key, display, uwi in rows:
        counts = cat.data_availability(key)
        data = ", ".join(
            f"{n} {label}" for label, n in counts.items() if n and label != "files"
        )
        uwi_txt = f"  [{uwi}]" if uwi else ""
        lines.append(f"  - {display}{uwi_txt}: {data or 'no extracted records'}")
    lines.append("\nBuild a cross-section with: wellcrawler cross-section -w <well1,well2,...>")
    return "\n".join(lines)


def wells_with_shows(cat: Catalog, fluid: Optional[str] = None) -> str:
    sql = (
        "SELECT w.display_name, s.fluid, COUNT(*) FROM shows s "
        "JOIN wells w ON w.well_key = s.well_key"
    )
    params: list = []
    if fluid:
        sql += " WHERE s.fluid = ?"
        params.append(fluid)
    sql += " GROUP BY w.display_name, s.fluid ORDER BY w.display_name"
    rows = cat.conn.execute(sql, params).fetchall()
    if not rows:
        qualifier = f" {fluid}" if fluid else ""
        return f"No wells with{qualifier} shows in the archive."
    lines = ["Wells with reported shows:"]
    for display, flu, count in rows:
        lines.append(f"  - {display}: {count} {flu} show(s)")
    return "\n".join(lines)


def well_tops(cat: Catalog, well_key: str) -> str:
    display = _display(cat, well_key)
    rows = cat.conn.execute(
        "SELECT formation, depth_md, unit, source_id, page, line_no, excerpt "
        "FROM tops WHERE well_key=? ORDER BY depth_md", (well_key,)
    ).fetchall()
    if not rows:
        return f"No formation tops recorded for {display}."
    lines = [f"Formation tops for {display}:"]
    for formation, depth, unit, source_id, page, line_no, excerpt in rows:
        prov = _prov(cat, source_id, page, line_no)
        lines.append(f"  {formation:<18} {depth:>9.1f} {unit}   {prov}")
    return "\n".join(lines)


def well_shows(cat: Catalog, well_key: str) -> str:
    display = _display(cat, well_key)
    rows = cat.conn.execute(
        "SELECT top_md, base_md, fluid, description, source_id, page, line_no "
        "FROM shows WHERE well_key=? ORDER BY top_md", (well_key,)
    ).fetchall()
    if not rows:
        return f"No shows recorded for {display}."
    lines = [f"Reported shows for {display}:"]
    for top, base, fluid, desc, source_id, page, line_no in rows:
        prov = _prov(cat, source_id, page, line_no)
        interval = f"{top:.1f}" if top == base else f"{top:.1f}-{base:.1f}"
        lines.append(f"  {interval:>12} m  [{fluid}] {desc[:70]}")
        lines.append(f"               {prov}")
    return "\n".join(lines)


def formation_tops(cat: Catalog, formation: str, well_key: Optional[str]) -> str:
    sql = (
        "SELECT w.display_name, t.depth_md, t.unit, t.source_id, t.page, t.line_no "
        "FROM tops t JOIN wells w ON w.well_key = t.well_key "
        "WHERE t.formation LIKE ?"
    )
    params: list = [f"%{formation}%"]
    if well_key:
        sql += " AND t.well_key = ?"
        params.append(well_key)
    sql += " ORDER BY t.depth_md"
    rows = cat.conn.execute(sql, params).fetchall()
    if not rows:
        scope = f" in {_display(cat, well_key)}" if well_key else ""
        return f"No top found for a formation matching '{formation}'{scope}."
    lines = [f"Tops matching '{formation}':"]
    for display, depth, unit, source_id, page, line_no in rows:
        lines.append(f"  {display:<18} {depth:>9.1f} {unit}   {_prov(cat, source_id, page, line_no)}")
    return "\n".join(lines)


def well_availability(cat: Catalog, well_key: str) -> str:
    display = _display(cat, well_key)
    counts = cat.data_availability(well_key)
    lines = [f"Data available for {display}:"]
    labels = {
        "files": "source files", "tops": "formation tops", "shows": "shows",
        "core": "core intervals", "curves": "log curves",
        "checkshots": "checkshots", "observations": "observations",
    }
    for key, label in labels.items():
        if counts.get(key):
            lines.append(f"  - {counts[key]} {label}")
    if len(lines) == 1:
        lines.append("  (nothing)")
    rows = cat.conn.execute(
        "SELECT path, kind, status FROM sources WHERE well_key=? ORDER BY path",
        (well_key,),
    ).fetchall()
    for path, kind, status in rows:
        lines.append(f"  file: {path}  [{kind}/{status}]")
    return "\n".join(lines)


def _suggest_xsec(cat: Catalog, q: str) -> str:
    keys = _well_list_tokens(cat, q)
    if not keys:
        all_keys = [k for k, _d, _u in cat.wells()]
        keys = all_keys[:3]
    names = ",".join(_display(cat, k) for k in keys)
    return (
        "To draw the cross-section, run:\n"
        f"  wellcrawler cross-section -w {names} -o xsec.svg\n"
        "(every top in the output links back to its source page)"
    )


def _fallback(cat: Catalog) -> str:
    n_wells = len(cat.wells())
    return (
        "I didn't understand that question. I can answer things like:\n"
        "  - which wells are in the archive?\n"
        "  - which wells have gas shows?\n"
        "  - what tops does AB-12-34 have?\n"
        "  - where is the Basal Sand?\n"
        "  - what data do we have for 12-34-056-25W4?\n"
        "  - build a cross-section of <well A>, <well B>, <well C>\n"
        f"(catalog holds {n_wells} wells)"
    )


# ----------------------------------------------------------------- helpers


def _fluid_filter(low: str) -> Optional[str]:
    if re.search(r"\bgas\b", low):
        return "gas"
    if re.search(r"\boil\b", low):
        return "oil"
    return None


def _find_well_token(cat: Catalog, q: str) -> Optional[str]:
    m = re.search(r"\bfor\s+([A-Za-z0-9][A-Za-z0-9\-/._ ]+)", q)
    candidates = []
    if m:
        candidates.append(m.group(1).strip().rstrip("?.!"))
    m2 = re.search(r"\bdoes\s+([A-Za-z0-9][A-Za-z0-9\-/._ ]+?)\s+have\b", q, re.IGNORECASE)
    if m2:
        candidates.insert(0, m2.group(1).strip())
    for candidate in candidates:
        resolved = cat.resolve_well(candidate)
        if resolved:
            return resolved
    # Last resort: any alias/substring appearing verbatim in the question.
    for alias, key in cat.conn.execute("SELECT alias, well_key FROM well_aliases"):
        if alias and alias.lower() in q.lower():
            return key
    for key, display, _uwi in cat.wells():
        if display and display.lower() in q.lower():
            return key
    return None


def _well_list_tokens(cat: Catalog, q: str) -> list:
    after = re.split(r"\b(?:of|with|for|using)\b", q, maxsplit=1, flags=re.IGNORECASE)
    if len(after) < 2:
        return []
    tail = after[1]
    keys = []
    for token in re.split(r",| and ", tail):
        token = token.strip().rstrip("?.!")
        if not token:
            continue
        resolved = cat.resolve_well(token)
        if resolved and resolved not in keys:
            keys.append(resolved)
    return keys


def _formation_token(cat: Catalog, q: str) -> Optional[str]:
    m = re.search(r"where is\s+(?:the\s+)?([A-Za-z][A-Za-z0-9 \-']+?)(?:\s+in|\s+at|\?|$)", q, re.IGNORECASE)
    if not m:
        m = re.search(r"tops? (?:for|of) (?:the )?([A-Za-z][A-Za-z0-9 \-']+?)(?:\s+formation|\?|$)", q, re.IGNORECASE)
    if not m:
        return None
    candidate = m.group(1).strip()
    row = cat.conn.execute(
        "SELECT DISTINCT formation FROM tops WHERE formation LIKE ? ORDER BY formation",
        (f"%{candidate}%",),
    ).fetchone()
    return row[0] if row else candidate


def _display(cat: Catalog, well_key: str) -> str:
    row = cat.conn.execute(
        "SELECT display_name FROM wells WHERE well_key=?", (well_key,)
    ).fetchone()
    return row[0] if row else well_key


def _prov(cat: Catalog, source_id, page, line_no) -> str:
    src = cat.source_by_id(source_id)
    if not src:
        return ""
    loc = []
    if page:
        loc.append(f"p.{page}")
    if line_no:
        loc.append(f"L{line_no}")
    suffix = f" ({', '.join(loc)})" if loc else ""
    return f"← {src[1]}{suffix}"
