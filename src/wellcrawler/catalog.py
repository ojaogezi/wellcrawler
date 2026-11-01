"""SQLite catalog: every record, its well, and its provenance.

The catalog is the single source of truth after a crawl.  Every data table
carries ``source_id`` plus ``page``/``line_no``/``excerpt`` so any extracted
point can be traced back to the exact spot in the original file — the
"one click from its source page" promise.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,      -- relative to crawl root
    kind TEXT NOT NULL,
    status TEXT NOT NULL,           -- ok | failed | unknown
    error TEXT,
    sha1 TEXT,
    size INTEGER,
    mtime REAL,
    well_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_sources_well ON sources(well_key);
CREATE TABLE IF NOT EXISTS wells (
    well_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    uwi TEXT
);
CREATE TABLE IF NOT EXISTS well_aliases (
    alias TEXT PRIMARY KEY,
    well_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tops (
    id INTEGER PRIMARY KEY,
    well_key TEXT NOT NULL,
    formation TEXT NOT NULL,
    depth_md REAL NOT NULL,
    unit TEXT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    page INTEGER,
    line_no INTEGER,
    excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_tops_well ON tops(well_key);
CREATE INDEX IF NOT EXISTS idx_tops_formation ON tops(formation);
CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY,
    well_key TEXT NOT NULL,
    top_md REAL NOT NULL,
    base_md REAL,
    fluid TEXT,
    description TEXT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    page INTEGER,
    line_no INTEGER,
    excerpt TEXT
);
CREATE INDEX IF NOT EXISTS idx_shows_well ON shows(well_key);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    well_key TEXT NOT NULL,
    kind TEXT,
    depth_md REAL,
    text TEXT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    page INTEGER,
    line_no INTEGER,
    excerpt TEXT
);
CREATE TABLE IF NOT EXISTS core_intervals (
    id INTEGER PRIMARY KEY,
    well_key TEXT NOT NULL,
    top_md REAL NOT NULL,
    base_md REAL NOT NULL,
    lithology TEXT,
    porosity REAL,
    permeability REAL,
    description TEXT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    line_no INTEGER
);
CREATE INDEX IF NOT EXISTS idx_core_well ON core_intervals(well_key);
CREATE TABLE IF NOT EXISTS curves (
    id INTEGER PRIMARY KEY,
    well_key TEXT NOT NULL,
    mnemonic TEXT NOT NULL,
    units TEXT,
    n_samples INTEGER,
    min_value REAL,
    max_value REAL,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_curves_well ON curves(well_key);
CREATE TABLE IF NOT EXISTS checkshots (
    id INTEGER PRIMARY KEY,
    well_key TEXT NOT NULL,
    n_points INTEGER,
    md_min REAL,
    md_max REAL,
    twt_min REAL,
    twt_max REAL,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE
);
"""

# Tables whose rows belong to a source and are wiped on re-ingest.
RECORD_TABLES = ("tops", "shows", "observations", "core_intervals", "curves", "checkshots")


def open_catalog(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@dataclass
class Catalog:
    """Thin convenience wrapper used by the CLI and renderer."""

    conn: sqlite3.Connection
    root: str = ""

    @classmethod
    def open(cls, path: str) -> "Catalog":
        conn = open_catalog(path)
        row = conn.execute("SELECT value FROM meta WHERE key='root'").fetchone()
        return cls(conn=conn, root=row[0] if row else "")

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------- writes

    def upsert_source(self, path: str, kind: str, status: str, error=None, sha1=None,
                      size=None, mtime=None, well_key=None) -> int:
        self.conn.execute(
            "INSERT INTO sources(path, kind, status, error, sha1, size, mtime, well_key) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, status=excluded.status, "
            "error=excluded.error, sha1=excluded.sha1, size=excluded.size, "
            "mtime=excluded.mtime, well_key=excluded.well_key",
            (path, kind, status, error, sha1, size, mtime, well_key),
        )
        # lastrowid is unreliable after an upsert-update; always re-read it.
        row = self.conn.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()
        return row[0]

    def purge_source_records(self, source_id: int) -> None:
        for table in RECORD_TABLES:
            self.conn.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))

    def remove_missing(self, live_paths: list) -> int:
        """Drop sources (cascading to their records) whose path vanished."""
        placeholders = ",".join("?" * len(live_paths)) or "''"
        cur = self.conn.execute(
            f"DELETE FROM sources WHERE path NOT IN ({placeholders})", live_paths
        )
        return cur.rowcount or 0

    def upsert_well(self, well_key: str, display_name: str, uwi=None) -> None:
        self.conn.execute(
            "INSERT INTO wells(well_key, display_name, uwi) VALUES(?,?,?) "
            "ON CONFLICT(well_key) DO UPDATE SET "
            "display_name=excluded.display_name, uwi=COALESCE(excluded.uwi, uwi)",
            (well_key, display_name, uwi),
        )

    def add_alias(self, alias: str, well_key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO well_aliases(alias, well_key) VALUES(?,?)",
            (alias, well_key),
        )

    def insert_records(self, table: str, rows: list) -> None:
        if not rows:
            return
        cols = sorted(rows[0].keys())
        placeholders = ",".join("?" * len(cols))
        self.conn.executemany(
            f"INSERT INTO {table}({','.join(cols)}) VALUES({placeholders})",
            [tuple(row[c] for c in cols) for row in rows],
        )

    def commit(self) -> None:
        self.conn.commit()

    # -------------------------------------------------------------- reads

    def source_by_id(self, source_id: int):
        return self.conn.execute(
            "SELECT id, path, kind, status, error FROM sources WHERE id=?", (source_id,)
        ).fetchone()

    def resolve_source_path(self, source_id: int) -> Optional[str]:
        import os

        row = self.conn.execute("SELECT path FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        return os.path.join(self.root, row[0]) if self.root else row[0]

    def wells(self):
        return self.conn.execute(
            "SELECT w.well_key, w.display_name, w.uwi FROM wells w ORDER BY w.display_name"
        ).fetchall()

    def resolve_well(self, query: str) -> Optional[str]:
        """Accept a well key, alias, display name or fuzzy substring."""
        q = query.strip()
        row = self.conn.execute(
            "SELECT well_key FROM wells WHERE well_key=? OR display_name=? COLLATE NOCASE",
            (q, q),
        ).fetchone()
        if row:
            return row[0]
        row = self.conn.execute(
            "SELECT well_key FROM well_aliases WHERE alias=?", (q.upper(),)
        ).fetchone()
        if row:
            return row[0]
        norm = "".join(ch for ch in q.upper() if ch.isalnum())
        row = self.conn.execute(
            "SELECT well_key FROM wells WHERE REPLACE(REPLACE(REPLACE("
            "REPLACE(REPLACE(UPPER(display_name),'-',''),'/',''),' ',''),'_',''),'.','')=?",
            (norm,),
        ).fetchone()
        if row:
            return row[0]
        return None

    def data_availability(self, well_key: str) -> dict:
        counts = {}
        for table, label in (
            ("tops", "tops"), ("shows", "shows"), ("core_intervals", "core"),
            ("curves", "curves"), ("checkshots", "checkshots"),
            ("observations", "observations"),
        ):
            row = self.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE well_key=?", (well_key,)
            ).fetchone()
            counts[label] = row[0]
        row = self.conn.execute(
            "SELECT COUNT(*) FROM sources WHERE well_key=? AND status='ok'", (well_key,)
        ).fetchone()
        counts["files"] = row[0]
        return counts
