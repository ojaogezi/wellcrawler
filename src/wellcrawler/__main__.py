"""Command-line interface for wellcrawler."""
from __future__ import annotations

import argparse
import glob
import os
import sys

from . import __version__, query
from .catalog import Catalog
from .crawl import crawl
from .xsec import render as render_xsec

DEFAULT_CATALOG = os.path.join(".wellcrawler", "catalog.db")


def _find_catalog(explicit: "str | None") -> str:
    """Locate the catalog: explicit flag > env > ./<nearby>/.wellcrawler/catalog.db."""
    if explicit:
        return explicit
    env = os.environ.get("WELLCRAWLER_CATALOG")
    if env:
        return env
    candidates = []
    for depth in (0, 1, 2):
        pattern = os.path.join(*(["*"] * depth + [".wellcrawler", "catalog.db"]))
        candidates.extend(glob.glob(pattern))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        listing = "\n  ".join(candidates)
        sys.exit(f"multiple catalogs found:\n  {listing}\npick one with --catalog")
    return DEFAULT_CATALOG


def _open(explicit: "str | None") -> Catalog:
    path = _find_catalog(explicit)
    if not os.path.exists(path):
        sys.exit(
            f"catalog not found at {path!r} — run `wellcrawler crawl <folder>` first "
            f"(or pass --catalog)"
        )
    return Catalog.open(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="wellcrawler",
        description="Crawl messy subsurface archives and standardize them by well.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("crawl", help="ingest a folder tree into the catalog")
    p.add_argument("root", help="folder to crawl (e.g. the shared-drive temp(14))")
    p.add_argument("-o", "--catalog", default=None, help="catalog path "
                   f"(default: <root>/{DEFAULT_CATALOG})")
    p.add_argument("-q", "--quiet", action="store_true", help="do not list each file")

    p = sub.add_parser("wells", help="list wells and what data each has")
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    p = sub.add_parser("well", help="show everything recorded for one well")
    p.add_argument("name", help="well display name, alias, UWI or key")
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    p = sub.add_parser("tops", help="list formation tops (optionally filtered)")
    p.add_argument("-w", "--well", default=None)
    p.add_argument("-f", "--formation", default=None)
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    p = sub.add_parser("shows", help="list reported hydrocarbon shows")
    p.add_argument("-w", "--well", default=None)
    p.add_argument("--fluid", default=None, choices=["gas", "oil", "fluorescence", "show"])
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    p = sub.add_parser("sources", help="list every source file and its ingest status")
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    p = sub.add_parser("cross-section", aliases=["xsec"],
                       help="render a cross-section SVG with clickable provenance")
    p.add_argument("-w", "--wells", required=True, help="comma-separated well names")
    p.add_argument("-o", "--out", default="xsec.svg")
    p.add_argument("--max-depth", type=float, default=None)
    p.add_argument("--title", default=None)
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    p = sub.add_parser("ask", help="ask a plain-English question about the archive")
    p.add_argument("question", help='e.g. "which wells have gas shows?"')
    p.add_argument("-c", "--catalog", default=None,
                   help="catalog path (default: auto-discover .wellcrawler/catalog.db)")

    args = parser.parse_args(argv)

    if args.cmd == "crawl":
        crawl(args.root, catalog_path=args.catalog, verbose=not args.quiet)
        return 0

    cat = _open(args.catalog)

    if args.cmd == "wells":
        print(query.list_wells(cat))
        return 0

    if args.cmd == "well":
        key = cat.resolve_well(args.name)
        if not key:
            sys.exit(f"no well matching {args.name!r}")
        print(query.well_availability(cat, key))
        print()
        print(query.well_tops(cat, key))
        print()
        print(query.well_shows(cat, key))
        return 0

    if args.cmd == "tops":
        if args.well or args.formation:
            well_key = cat.resolve_well(args.well) if args.well else None
            if args.well and not well_key:
                sys.exit(f"no well matching {args.well!r}")
            print(query.formation_tops(cat, args.formation, well_key)
                  if args.formation else query.well_tops(cat, well_key))
            return 0
        rows = cat.conn.execute(
            "SELECT w.display_name, t.formation, t.depth_md, t.unit, s.path, t.page, t.line_no "
            "FROM tops t JOIN wells w ON w.well_key=t.well_key "
            "JOIN sources s ON s.id=t.source_id ORDER BY w.display_name, t.depth_md"
        ).fetchall()
        for display, formation, depth, unit, path, page, line in rows:
            loc = f" (p.{page}, L{line})" if (page or line) else ""
            print(f"{display:<20} {formation:<18} {depth:>9.1f} {unit:<2}  <- {path}{loc}")
        return 0

    if args.cmd == "shows":
        where, params = [], []
        if args.well:
            key = cat.resolve_well(args.well)
            if not key:
                sys.exit(f"no well matching {args.well!r}")
            where.append("t.well_key=?")
            params.append(key)
        if args.fluid:
            where.append("t.fluid=?")
            params.append(args.fluid)
        sql = ("SELECT w.display_name, t.top_md, t.base_md, t.fluid, t.description, "
               "s.path, t.page, t.line_no FROM shows t "
               "JOIN wells w ON w.well_key=t.well_key JOIN sources s ON s.id=t.source_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY w.display_name, t.top_md"
        for display, top, base, fluid, desc, path, page, line in cat.conn.execute(sql, params):
            interval = f"{top:g}" if top == (base or top) else f"{top:g}-{base:g}"
            loc = f" (p.{page}, L{line})" if (page or line) else ""
            print(f"{display:<20} {interval:>14} m  [{fluid:<12}] {desc[:60]}")
            print(f"{'':<20} <- {path}{loc}")
        return 0

    if args.cmd == "sources":
        rows = cat.conn.execute(
            "SELECT path, kind, status, error, well_key FROM sources ORDER BY path"
        ).fetchall()
        for path, kind, status, error, well in rows:
            suffix = f"  [{error}]" if error else ""
            well_txt = f"  -> {well}" if well else ""
            print(f"{status:<8} {kind:<10} {path}{well_txt}{suffix}")
        return 0

    if args.cmd in ("cross-section", "xsec"):
        keys = []
        for token in args.wells.split(","):
            token = token.strip()
            if not token:
                continue
            key = cat.resolve_well(token)
            if not key:
                sys.exit(f"no well matching {token!r}")
            keys.append(key)
        if not keys:
            sys.exit("no wells given")
        out = render_xsec(cat, keys, args.out, max_depth=args.max_depth, title=args.title)
        print(f"cross-section written to {out}")
        print("open it in a browser — every top/show links to its source file")
        return 0

    if args.cmd == "ask":
        print(query.answer(cat, args.question))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
