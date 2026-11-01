"""Crawl orchestration: walk a messy tree, ingest everything recognizable.

The crawl is idempotent (unchanged files are skipped by sha1) and never
aborts on a bad file — failures are recorded in the sources ledger so the
summary can tell you exactly what did not make it and why.
"""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from . import classify
from .catalog import Catalog
from .registry import Registry
from .tables import TableError

SKIP_DIRS = {".wellcrawler", ".git", "__pycache__", "node_modules", ".DS_Store"}
MAX_BYTES = 200 * 1024 * 1024  # hard stop for absurd files


@dataclass
class CrawlStats:
    files_seen: int = 0
    skipped_unchanged: int = 0
    ingested: int = 0
    unknown: int = 0
    failed: int = 0
    wells: int = 0
    records: int = 0
    failures: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)


def crawl(root: str, catalog_path: Optional[str] = None, verbose: bool = True) -> CrawlStats:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    catalog_path = catalog_path or os.path.join(root, ".wellcrawler", "catalog.db")
    os.makedirs(os.path.dirname(catalog_path), exist_ok=True)

    cat = Catalog.open(catalog_path)
    cat.set_meta("root", root)
    stats = CrawlStats()
    registry = Registry()
    live_paths = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            live_paths.append(rel)
            stats.files_seen += 1
            if verbose:
                print(f"  {rel}", file=sys.stderr)
            _ingest_file(cat, registry, stats, root, rel, path)

    _flush_registry(cat, registry)
    removed = cat.remove_missing(live_paths)
    cat.commit()
    stats.wells = len(cat.wells())
    _print_summary(stats, catalog_path, removed)
    return stats


def _ingest_file(cat, registry, stats, root, rel, path) -> None:
    try:
        st = os.stat(path)
    except OSError as exc:
        stats.failed += 1
        stats.failures.append((rel, str(exc)))
        cat.upsert_source(rel, classify.UNKNOWN, "failed", error=str(exc))
        return
    if st.st_size > MAX_BYTES:
        stats.unknown += 1
        stats.unknowns.append((rel, "file too large"))
        cat.upsert_source(rel, classify.UNKNOWN, "unknown", error="file too large")
        return

    with open(path, "rb") as fh:
        blob = fh.read()
    sha1 = hashlib.sha1(blob).hexdigest()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        kind = classify.classify(path)
        if kind.is_data:
            # Only LAS realistically arrives as latin-1; try it once.
            try:
                text = blob.decode("latin-1")
            except UnicodeDecodeError:
                text = None
        else:
            text = None
    if text is None:
        stats.unknown += 1
        stats.unknowns.append((rel, "binary or undecodable"))
        cat.upsert_source(rel, classify.UNKNOWN, "unknown", error="binary",
                          sha1=sha1, size=st.st_size, mtime=st.st_mtime)
        return

    previous = cat.conn.execute(
        "SELECT id, sha1, size FROM sources WHERE path=?", (rel,)
    ).fetchone()
    if previous and previous[1] == sha1 and previous[2] == st.st_size:
        stats.skipped_unchanged += 1
        # Keep well links alive across re-crawls even when skipped.
        if previous is not None:
            cat.conn.execute(
                "UPDATE sources SET mtime=? WHERE id=?", (st.st_mtime, previous[0])
            )
        return

    kind = classify.classify(path)
    source_id = cat.upsert_source(rel, kind.kind, "ok", sha1=sha1, size=st.st_size,
                                  mtime=st.st_mtime)
    cat.purge_source_records(source_id)

    if not kind.is_data:
        stats.unknown += 1
        stats.unknowns.append((rel, kind.reason))
        cat.upsert_source(rel, kind.kind, "unknown", error=kind.reason, sha1=sha1,
                          size=st.st_size, mtime=st.st_mtime)
        return

    try:
        records, well = _extract(kind.kind, text, rel, registry)
    except Exception as exc:  # parser errors are data, not crashers
        stats.failed += 1
        stats.failures.append((rel, f"{type(exc).__name__}: {exc}"))
        cat.upsert_source(rel, kind.kind, "failed", error=str(exc), sha1=sha1,
                          size=st.st_size, mtime=st.st_mtime)
        cat.purge_source_records(source_id)
        return

    well_key = well.well_key if well is not None else None
    for table, rows in records.items():
        for row in rows:
            row["source_id"] = source_id
        cat.insert_records(table, rows)
        stats.records += len(rows)
    if well_key:
        cat.conn.execute("UPDATE sources SET well_key=? WHERE id=?", (well_key, source_id))
    stats.ingested += 1


def _extract(kind: str, text: str, rel: str, registry: Registry):
    """Dispatch to the right parser; return ({table: rows}, well|None)."""
    if kind == classify.LAS:
        return _extract_las(text, rel, registry)
    if kind == classify.CHECKSHOT:
        return _extract_checkshot(text, rel, registry)
    if kind == classify.CORE_DESC:
        return _extract_core(text, rel, registry)
    if kind == classify.TOPS_CSV:
        return _extract_tops(text, rel, registry)
    if kind == classify.REPORT:
        return _extract_report(text, rel, registry)
    raise ValueError(f"no extractor for kind {kind}")


def _register(registry: Registry, names, uwi=None, priority=Registry.PRIORITY_FILENAME):
    names = [n for n in names if n]
    if not names and not uwi:
        return None
    return registry.register(names, uwi=uwi, priority=priority)


def _extract_las(text, rel, registry):
    from . import las as las_mod

    parsed = las_mod.parse(text)
    names = [parsed.well, _filename_well(rel)]
    well = _register(registry, names, uwi=parsed.uwi, priority=Registry.PRIORITY_LAS)
    records = {"curves": []}
    for curve in parsed.curves:
        values = [v for v in parsed.columns.get(curve.mnemonic, []) if v is not None]
        records["curves"].append({
            "well_key": well.well_key if well else None,
            "mnemonic": curve.mnemonic,
            "units": curve.unit,
            "n_samples": len(parsed.columns.get(curve.mnemonic, [])),
            "min_value": min(values) if values else None,
            "max_value": max(values) if values else None,
            "source_id": None,  # patched by caller via source_id column? no—set below
        })
    return records, well


def _extract_checkshot(text, rel, registry):
    from .tables import parse_checkshot

    cs = parse_checkshot(text)
    well = _register(registry, [_filename_well(rel)], priority=Registry.PRIORITY_TABLE)
    mds = [p[0] for p in cs.points]
    twts = [p[2] for p in cs.points]
    return {
        "checkshots": [{
            "well_key": well.well_key if well else None,
            "n_points": len(cs.points),
            "md_min": min(mds), "md_max": max(mds),
            "twt_min": min(twts), "twt_max": max(twts),
            "source_id": None,
        }]
    }, well


def _extract_core(text, rel, registry):
    from .tables import parse_core_desc

    intervals = parse_core_desc(text)
    well = _register(registry, [_filename_well(rel)], priority=Registry.PRIORITY_TABLE)
    rows = [{
        "well_key": well.well_key if well else None,
        "top_md": i.top_md, "base_md": i.base_md, "lithology": i.lithology,
        "porosity": i.porosity, "permeability": i.permeability,
        "description": i.description, "line_no": i.line_no,
        "source_id": None,
    } for i in intervals]
    return {"core_intervals": rows}, well


def _extract_tops(text, rel, registry):
    from .tables import parse_tops_csv

    tops = parse_tops_csv(text)
    well = _register(registry, [_filename_well(rel)], priority=Registry.PRIORITY_TABLE)
    rows = [{
        "well_key": well.well_key if well else None,
        "formation": t.formation, "depth_md": t.depth_md, "unit": t.unit,
        "page": None, "line_no": t.line_no, "excerpt": None,
        "source_id": None,
    } for t in tops]
    return {"tops": rows}, well


def _extract_report(text, rel, registry):
    from .report import parse_report

    result = parse_report(text)
    names = [result.well_hint, _filename_well(rel)]
    well = _register(registry, names, priority=Registry.PRIORITY_REPORT)
    records = {
        "tops": [{
            "well_key": well.well_key if well else None,
            "formation": t.formation, "depth_md": t.depth_md, "unit": t.unit,
            "page": t.page, "line_no": t.line_no, "excerpt": t.excerpt,
            "source_id": None,
        } for t in result.tops],
        "shows": [{
            "well_key": well.well_key if well else None,
            "top_md": s.top_md, "base_md": s.base_md, "fluid": s.fluid,
            "description": s.description, "page": s.page, "line_no": s.line_no,
            "excerpt": s.excerpt, "source_id": None,
        } for s in result.shows],
        "observations": [{
            "well_key": well.well_key if well else None,
            "kind": o.kind, "depth_md": o.depth_md, "text": o.text,
            "page": o.page, "line_no": o.line_no, "excerpt": o.excerpt,
            "source_id": None,
        } for o in result.observations],
    }
    return records, well


def _filename_well(rel: str) -> str:
    from .registry import name_from_filename

    return name_from_filename(rel)


def _flush_registry(cat: Catalog, registry: Registry) -> None:
    for well in registry.wells():
        well.finalize_display()
        if not well.display_name:
            well.display_name = well.well_key
        cat.upsert_well(well.well_key, well.display_name, well.uwi)
        for alias in sorted(well.aliases):
            cat.add_alias(alias, well.well_key)


def _print_summary(stats: CrawlStats, catalog_path: str, removed: int) -> None:
    print(f"\nCrawl complete.")
    print(f"  files seen      : {stats.files_seen}")
    print(f"  ingested        : {stats.ingested}")
    print(f"  skipped (same)  : {stats.skipped_unchanged}")
    print(f"  unknown         : {stats.unknown}")
    print(f"  failed          : {stats.failed}")
    print(f"  wells           : {stats.wells}")
    print(f"  records         : {stats.records}")
    if removed:
        print(f"  removed (gone)  : {removed}")
    print(f"  catalog         : {catalog_path}")
    for rel, reason in stats.failures:
        print(f"  FAILED {rel}: {reason}")
    for rel, reason in stats.unknowns:
        print(f"  unknown {rel}: {reason}")
