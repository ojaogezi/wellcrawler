"""Cross-section renderer: wells side by side as SVG, provenance embedded.

Each well gets a strip panel on a shared measured-depth axis: a log-curve
track (re-read from the source LAS), a core-lithology column, formation-top
lines and hydrocarbon-show bands.  Every top and show is wrapped in a
clickable link to its source file, with a hover tooltip naming the exact
page and line — "one click from its source page".
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

from . import las as las_mod
from .catalog import Catalog

CURVE_PREFERENCE = (
    "GR", "GRGC", "DGR", "GAMMA", "DT", "AC", "RHOB", "DEN", "DENB",
    "ILD", "LLD", "RT", "RES", "NPHI", "SP",
)

FLUID_COLORS = {
    "gas": "#16a34a",
    "oil": "#1f2937",
    "fluorescence": "#d97706",
    "show": "#7c3aed",
}

LITHO_COLORS = [
    (("sand", "sst", "sandstone"), "#fbbf24"),
    (("shale", "shl", "mudstone", "clay"), "#9ca3af"),
    (("silt", "siltstone"), "#f472b6"),
    (("limestone", "lst", "lime"), "#60a5fa"),
    (("dolomite", "dol"), "#2dd4bf"),
    (("coal", "lignite"), "#374151"),
    (("anhydrite", "salt", "halite"), "#e5e7eb"),
]

MARGIN_L = 64
HEADER_H = 144
PLOT_H = 760
PANEL_W = 360
TRACK_W = 150
CORE_W = 26
FOOTER_LH = 15


def render(cat: Catalog, well_keys: list, out_path: str, max_depth: Optional[float] = None,
           title: Optional[str] = None) -> str:
    panels = [_panel_data(cat, key) for key in well_keys]
    panels = [p for p in panels if p]
    if not panels:
        raise ValueError("no data found for the requested wells")

    d_max = max_depth or _nice_depth(max(p["max_depth"] for p in panels))
    width = MARGIN_L + PANEL_W * len(panels) + 16
    footer_lines = _footer_lines(panels)
    height = HEADER_H + PLOT_H + 40 + FOOTER_LH * len(footer_lines) + 24

    def y(depth: float) -> float:
        return HEADER_H + (depth / d_max) * PLOT_H

    parts = [_svg_open(width, height)]
    parts.append(_header(title or "Cross-section", panels, d_max))
    parts.append(_depth_axis(d_max, width, y))

    for i, panel in enumerate(panels):
        x0 = MARGIN_L + PANEL_W * i
        parts.append(_panel(cat, panel, x0, y, d_max))
        if i:
            parts.append(
                f'<line x1="{x0 - 8}" y1="{HEADER_H}" x2="{x0 - 8}" '
                f'y2="{HEADER_H + PLOT_H}" stroke="#e5e7eb"/>'
            )

    parts.append(_footer(footer_lines, width, height))
    parts.append("</svg>\n")
    svg = "\n".join(parts)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    return str(out)


# ------------------------------------------------------------ panel data


def _panel_data(cat: Catalog, well_key: str) -> Optional[dict]:
    display_row = cat.conn.execute(
        "SELECT display_name, uwi FROM wells WHERE well_key=?", (well_key,)
    ).fetchone()
    if not display_row:
        return None
    tops = cat.conn.execute(
        "SELECT formation, depth_md, unit, source_id, page, line_no, excerpt "
        "FROM tops WHERE well_key=? ORDER BY depth_md", (well_key,)
    ).fetchall()
    shows = cat.conn.execute(
        "SELECT top_md, base_md, fluid, description, source_id, page, line_no "
        "FROM shows WHERE well_key=? ORDER BY top_md", (well_key,)
    ).fetchall()
    core = cat.conn.execute(
        "SELECT top_md, base_md, lithology, porosity, permeability, description "
        "FROM core_intervals WHERE well_key=? ORDER BY top_md", (well_key,)
    ).fetchall()
    curve = _pick_curve(cat, well_key)

    depths = [r[1] for r in tops]
    depths += [r[0] for r in shows] + [r[1] or r[0] for r in shows]
    depths += [r[0] for r in core] + [r[1] for r in core]
    if curve:
        depths.append(curve["depth_max"])
    if not depths:
        return None  # a well with literally nothing depth-tied cannot be drawn
    return {
        "well_key": well_key,
        "display": display_row[0],
        "uwi": display_row[1],
        "tops": tops,
        "shows": shows,
        "core": core,
        "curve": curve,
        "max_depth": max(depths),
        "sources": _sources_used(cat, tops, shows),
    }


def _pick_curve(cat: Catalog, well_key: str) -> Optional[dict]:
    rows = cat.conn.execute(
        "SELECT c.mnemonic, c.units, s.path FROM curves c "
        "JOIN sources s ON s.id = c.source_id "
        "WHERE c.well_key=? AND s.status='ok' ORDER BY c.mnemonic", (well_key,)
    ).fetchall()
    if not rows:
        return None
    by_mnemonic = {r[0].upper(): r for r in rows}
    chosen = None
    for pref in CURVE_PREFERENCE:
        if pref in by_mnemonic:
            chosen = by_mnemonic[pref]
            break
    if chosen is None:
        chosen = rows[0]
    mnemonic, units, rel_path = chosen
    path = os.path.join(cat.root, rel_path) if cat.root else rel_path
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        parsed = las_mod.parse(text)
        col = mnemonic if mnemonic in parsed.columns else next(
            (m for m in parsed.columns if m.upper() == mnemonic.upper()), None
        )
        depths, values = parsed.samples(col) if col else ([], [])
    except Exception:
        return None
    if len(depths) < 2:
        return None
    return {
        "mnemonic": mnemonic,
        "units": units,
        "path": path,
        "depths": depths,
        "values": values,
        "depth_max": max(depths),
    }


def _sources_used(cat: Catalog, tops, shows) -> dict:
    used = {}
    for source_id in [r[3] for r in tops] + [r[4] for r in shows]:
        src = cat.source_by_id(source_id)
        if src:
            used[source_id] = (src[1], src[3])  # path, status
    return used


# ------------------------------------------------------------- svg pieces


def _svg_open(width: float, height: float) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif">'
        '<rect width="100%" height="100%" fill="white"/>'
    )


def _header(title: str, panels: list, d_max: float) -> str:
    out = [f'<text x="16" y="24" font-size="17" font-weight="600" fill="#111827">{escape(title)}</text>']
    out.append(
        f'<text x="16" y="44" font-size="11" fill="#6b7280">'
        f'{len(panels)} wells · equal spacing (not geographically scaled) · '
        f'measured depth 0–{d_max:g} m · click any top/show to open its source file</text>'
    )
    # Legend rows sit between the subtitle and the panel headers.
    out.append('<text x="16" y="66" font-size="10" fill="#6b7280">shows:</text>')
    x = 60
    for fluid, color in FLUID_COLORS.items():
        out.append(f'<rect x="{x}" y="57" width="10" height="10" fill="{color}" fill-opacity="0.45" stroke="{color}" stroke-width="0.5"/>')
        out.append(f'<text x="{x + 14}" y="66" font-size="10" fill="#374151">{fluid}</text>')
        x += 24 + 7 * len(fluid)
    out.append('<text x="16" y="82" font-size="10" fill="#6b7280">core:</text>')
    x = 60
    for names, color in LITHO_COLORS[:5]:
        label = names[0]
        out.append(f'<rect x="{x}" y="73" width="10" height="10" fill="{color}" stroke="#9ca3af" stroke-width="0.5"/>')
        out.append(f'<text x="{x + 14}" y="{82}" font-size="10" fill="#374151">{label}</text>')
        x += 24 + 7 * len(label)
    return "\n".join(out)


def _depth_axis(d_max: float, width: float, y) -> str:
    step = _nice_step(d_max)
    out = []
    depth = step
    while depth <= d_max + 1e-9:
        yy = y(depth)
        out.append(
            f'<line x1="{MARGIN_L - 6}" y1="{yy:.1f}" x2="{width - 8}" y2="{yy:.1f}" '
            f'stroke="#f3f4f6" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{MARGIN_L - 10}" y="{yy + 3:.1f}" font-size="9" text-anchor="end" '
            f'fill="#9ca3af">{depth:g}</text>'
        )
        depth += step
    out.append(
        f'<text x="{MARGIN_L - 10}" y="{HEADER_H - 8}" font-size="9" text-anchor="end" '
        f'fill="#6b7280" transform="rotate(-90 {MARGIN_L - 10} {HEADER_H - 8})">MD (m)</text>'
    )
    return "\n".join(out)


def _panel(cat: Catalog, panel: dict, x0: float, y, d_max: float) -> str:
    out = []
    track_x = x0 + 4
    core_x = track_x + TRACK_W + 6
    label_x = core_x + CORE_W + 8

    out.append(_panel_header(panel, x0, track_x))
    out.append(
        f'<rect x="{track_x}" y="{HEADER_H}" width="{TRACK_W}" height="{PLOT_H}" '
        f'fill="#fafafa" stroke="#d1d5db" stroke-width="0.75"/>'
    )
    out.append(
        f'<rect x="{core_x}" y="{HEADER_H}" width="{CORE_W}" height="{PLOT_H}" '
        f'fill="#ffffff" stroke="#d1d5db" stroke-width="0.75"/>'
    )

    out.append(_curve_polyline(panel, track_x, y))
    for top_md, base_md, lithology, porosity, permeability, desc in panel["core"]:
        out.append(_core_rect(core_x, top_md, base_md, lithology, porosity, permeability, desc, y))
    for formation, depth, unit, source_id, page, line_no, excerpt in panel["tops"]:
        out.append(_top_marker(cat, formation, depth, unit, source_id, page, line_no,
                               excerpt, track_x, core_x, label_x, y))
    for top_md, base_md, fluid, desc, source_id, page, line_no in panel["shows"]:
        out.append(_show_band(cat, top_md, base_md, fluid, desc, source_id, page,
                              line_no, track_x, y))
    return "\n".join(out)


def _panel_header(panel: dict, x0: float, track_x: float) -> str:
    uwi = f' · {panel["uwi"]}' if panel["uwi"] else ""
    curve = panel["curve"]
    curve_txt = (
        f'curve: {curve["mnemonic"]} ({curve["units"] or "unitless"})'
        if curve else "curve: none available"
    )
    return "\n".join([
        f'<text x="{x0 + 4}" y="{HEADER_H - 42}" font-size="13" font-weight="600" '
        f'fill="#111827">{escape(panel["display"])}{escape(uwi)}</text>',
        f'<text x="{x0 + 4}" y="{HEADER_H - 28}" font-size="10" fill="#6b7280">{escape(curve_txt)}</text>',
        f'<text x="{x0 + 4}" y="{HEADER_H - 14}" font-size="10" fill="#6b7280">'
        f'{len(panel["tops"])} tops · {len(panel["shows"])} shows · {len(panel["core"])} core intervals</text>',
    ])


def _curve_polyline(panel: dict, track_x: float, y) -> str:
    curve = panel["curve"]
    if not curve:
        mid = HEADER_H + PLOT_H / 2
        return (
            f'<text x="{track_x + TRACK_W / 2}" y="{mid}" font-size="10" text-anchor="middle" '
            f'fill="#9ca3af">no LAS curve</text>'
        )
    depths, values = curve["depths"], curve["values"]
    lo, hi = _percentile(values, 2), _percentile(values, 98)
    if hi <= lo:
        hi = lo + 1.0
    n_target = 300
    step = max(1, len(depths) // n_target)
    points = []
    for i in range(0, len(depths), step):
        d, v = depths[i], values[i]
        frac = min(1.0, max(0.0, (v - lo) / (hi - lo)))
        px = track_x + 2 + frac * (TRACK_W - 4)
        points.append(f"{px:.1f},{y(d):.1f}")
    return (
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#1d4ed8" '
        f'stroke-width="0.9" stroke-linejoin="round"/>'
    )


def _core_rect(core_x, top_md, base_md, lithology, porosity, permeability, desc, y) -> str:
    color = _lith_color(lithology)
    yy, hh = y(top_md), max(y(base_md) - y(top_md), 1.5)
    tip = f"{lithology or 'core'} {top_md:g}-{base_md:g} m"
    if porosity is not None:
        tip += f" · phi {porosity:g}%"
    if permeability is not None:
        tip += f" · k {permeability:g} mD"
    if desc:
        tip += f" · {desc}"
    return (
        f'<rect x="{core_x}" y="{yy:.1f}" width="{CORE_W}" height="{hh:.1f}" '
        f'fill="{color}" stroke="#6b7280" stroke-width="0.4">'
        f'<title>{escape(tip)}</title></rect>'
    )


def _top_marker(cat, formation, depth, unit, source_id, page, line_no, excerpt,
                track_x, core_x, label_x, y) -> str:
    yy = y(depth)
    href, tip = _provenance_link(cat, source_id, page, line_no, excerpt)
    dash = f'<line x1="{track_x}" y1="{yy:.1f}" x2="{core_x + CORE_W}" y2="{yy:.1f}" ' \
           f'stroke="#dc2626" stroke-width="1" stroke-dasharray="5 3"/>'
    label = (
        f'<text x="{label_x}" y="{yy - 3:.1f}" font-size="10" fill="#b91c1c">'
        f'{escape(formation)} {depth:g} {unit or "m"}</text>'
    )
    if href:
        return (
            f'<a href={quoteattr(href)} target="_blank">'
            f'<g><title>{escape(tip)}</title>{dash}{label}</g></a>'
        )
    return f'<g><title>{escape(tip)}</title>{dash}{label}</g>'


def _show_band(cat, top_md, base_md, fluid, desc, source_id, page, line_no,
               track_x, y) -> str:
    color = FLUID_COLORS.get(fluid, FLUID_COLORS["show"])
    yy = y(top_md)
    hh = max(y(base_md if base_md is not None else top_md) - yy, 3.0)
    href, tip = _provenance_link(cat, source_id, page, line_no, desc)
    band = (
        f'<rect x="{track_x}" y="{yy:.1f}" width="{TRACK_W}" height="{hh:.1f}" '
        f'fill="{color}" fill-opacity="0.28" stroke="{color}" stroke-width="0.6"/>'
    )
    label = ""
    if hh >= 13:
        label = (
            f'<text x="{track_x + 3}" y="{yy + hh / 2 + 3:.1f}" font-size="8.5" '
            f'fill="{color}">{escape(fluid)}</text>'
        )
    if href:
        return (
            f'<a href={quoteattr(href)} target="_blank">'
            f'<g><title>{escape(tip)}</title>{band}{label}</g></a>'
        )
    return f'<g><title>{escape(tip)}</title>{band}{label}</g>'


def _provenance_link(cat: Catalog, source_id, page, line_no, excerpt) -> tuple:
    src = cat.source_by_id(source_id)
    if not src:
        return "", "source missing"
    path = cat.resolve_source_path(source_id) or src[1]
    loc = []
    if page:
        loc.append(f"page {page}")
    if line_no:
        loc.append(f"line {line_no}")
    tip = f"{src[1]}" + (f" · {', '.join(loc)}" if loc else "")
    if excerpt:
        tip += f"\n\"{excerpt.strip()[:140]}\""
    try:
        href = Path(path).resolve().as_uri()
    except (OSError, ValueError):
        href = ""
    return href, tip


def _footer_lines(panels: list) -> list:
    lines = ["Sources (click any marker in the figure to open its file):"]
    for panel in panels:
        for source_id, (path, status) in sorted(panel["sources"].items(), key=lambda kv: kv[1][0]):
            lines.append(f"  {panel['display']}: {path}  [{status}]")
    return lines


def _footer(lines: list, width: float, height: float) -> str:
    y0 = HEADER_H + PLOT_H + 28
    out = [
        f'<line x1="16" y1="{y0 - 12}" x2="{width - 16}" y2="{y0 - 12}" stroke="#e5e7eb"/>',
        f'<text x="16" y="{y0}" font-size="10" font-weight="600" fill="#374151">{escape(lines[0])}</text>',
    ]
    for i, line in enumerate(lines[1:], start=1):
        out.append(
            f'<text x="24" y="{y0 + i * FOOTER_LH}" font-size="9.5" fill="#6b7280">'
            f'{escape(line)}</text>'
        )
    return "\n".join(out)


# ---------------------------------------------------------------- helpers


def _percentile(values: list, pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    k = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[k]


def _nice_depth(value: float) -> float:
    for candidate in (100, 200, 250, 500, 1000, 1500, 2000, 2500, 3000, 4000, 5000):
        if value <= candidate:
            return float(candidate)
    return float(int(value / 1000 + 1) * 1000)


def _nice_step(d_max: float) -> float:
    target = d_max / 12
    for step in (10, 25, 50, 100, 200, 250, 500, 1000):
        if step >= target:
            return float(step)
    return 1000.0


def _lith_color(lithology: str) -> str:
    low = (lithology or "").lower()
    for names, color in LITHO_COLORS:
        if any(name in low for name in names):
            return color
    return "#d1d5db"
