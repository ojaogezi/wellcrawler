"""End-to-end: crawl the demo temp(14) tree and use every consuming verb."""
from __future__ import annotations

import os
import sqlite3

import pytest

from wellcrawler.catalog import Catalog
from wellcrawler.crawl import crawl
from wellcrawler.query import answer
from wellcrawler.xsec import render


@pytest.fixture()
def catalog(demo_tree, tmp_path):
    db = str(tmp_path / "catalog.db")
    stats = crawl(str(demo_tree), catalog_path=db, verbose=False)
    return demo_tree, db, Catalog.open(db), stats


def test_crawl_ingests_known_files(catalog):
    _tree, _db, _cat, stats = catalog
    assert stats.files_seen == 10
    assert stats.ingested == 7          # 3 wells' files minus junk/broken
    assert stats.unknown == 2           # lunch notes + Thumbs.db
    assert stats.failed == 1            # broken wrapped LAS


def test_collapse_to_three_wells(catalog):
    _tree, _db, cat, _stats = catalog
    wells = {w[1]: w[2] for w in cat.wells()}
    assert set(wells) == {"AB-12-34", "CD-56-78", "EF-90-12"}
    assert wells["AB-12-34"] == "100/12-34-056-25W4/0"
    assert wells["CD-56-78"] == "100/14-22-056-25W4/0"


def test_files_merge_into_single_well(catalog):
    """LAS + checkshot + core CSV all land on well AB-12-34."""
    _tree, _db, cat, _stats = catalog
    key = cat.resolve_well("AB-12-34")
    rows = cat.conn.execute(
        "SELECT path FROM sources WHERE well_key=? AND status='ok'", (key,)
    ).fetchall()
    assert {r[0] for r in rows} == {
        "FINAL - do not delete/12-34-056-25W4_AB-12-34_las_FINAL2.las",
        "checkshots exported by intern/AB-12-34_chk_new.csv",
        "core/AB-12-34 core descriptions vFINAL.csv",
    }


def test_records_by_well(catalog):
    _tree, _db, cat, _stats = catalog
    ab = cat.resolve_well("AB-12-34")
    cd = cat.resolve_well("CD-56-78")
    ef = cat.resolve_well("EF-90-12")

    assert cat.data_availability(ab) == {
        "tops": 0, "shows": 0, "core": 5, "curves": 3,
        "checkshots": 1, "observations": 0, "files": 3,
    }
    cd_avail = cat.data_availability(cd)
    assert cd_avail["tops"] == 4
    assert cd_avail["shows"] == 3
    assert cd_avail["observations"] == 1
    assert cd_avail["core"] == 4
    assert cat.data_availability(ef)["tops"] == 4


def test_provenance_chain_from_top_to_source(catalog):
    tree, _db, cat, _stats = catalog
    row = cat.conn.execute(
        "SELECT t.formation, t.depth_md, t.page, t.line_no, s.path "
        "FROM tops t JOIN sources s ON s.id = t.source_id "
        "WHERE t.formation = 'Viking' AND s.path LIKE '%legacy%'"
    ).fetchone()
    assert row is not None
    formation, depth, page, line_no, rel = row
    assert depth == 1122.0
    assert page == 1 and line_no
    assert (tree / rel).exists()


def test_failed_and_unknown_ledgered(catalog):
    _tree, _db, cat, _stats = catalog
    rows = {r[0]: r for r in cat.conn.execute(
        "SELECT path, kind, status, error FROM sources WHERE status != 'ok'"
    ).fetchall()}
    broken = rows["logs from contractor (old)/broken wrapped log.las"]
    assert broken[1] == "las" and broken[2] == "failed" and "WRAP" in broken[3]
    assert "notes_about_lunch.txt" in rows
    assert "Thumbs.db" in rows


def test_recrawl_is_idempotent(catalog):
    tree, db, _cat, stats = catalog
    stats2 = crawl(str(tree), catalog_path=db, verbose=False)
    assert stats2.skipped_unchanged == stats.files_seen - stats2.failed - stats2.unknown
    assert stats2.ingested == 0
    reopened = Catalog.open(db)
    assert reopened.conn.execute("SELECT COUNT(*) FROM tops").fetchone()[0] == 8


def test_ask_intents(catalog):
    _tree, _db, cat, _stats = catalog

    listing = answer(cat, "which wells are in the archive?")
    for name in ("AB-12-34", "CD-56-78", "EF-90-12"):
        assert name in listing

    shows = answer(cat, "which wells have gas shows?")
    assert "CD-56-78" in shows

    tops = answer(cat, "what tops does CD-56-78 have?")
    assert "Viking" in tops and "1122.0" in tops
    assert "legacy report CD-56-78.txt" in tops

    where = answer(cat, "where is the Basal Sand?")
    assert "1180.5" in where and "1150.2" in where

    avail = answer(cat, "what data do we have for AB-12-34?")
    assert "checkshot" in avail and "curves" in avail

    xsec = answer(cat, "build a cross-section of AB-12-34, CD-56-78, EF-90-12")
    assert "cross-section" in xsec and "AB-12-34" in xsec

    assert "didn't understand" in answer(cat, "what is the meaning of life?")


def test_cross_section_svg(catalog):
    tree, _db, cat, _stats = catalog
    keys = [cat.resolve_well(n) for n in ("AB-12-34", "CD-56-78", "EF-90-12")]
    out = os.path.join(str(tree), "xsec.svg")
    path = render(cat, keys, out)
    svg = open(path, encoding="utf-8").read()
    for name in ("AB-12-34", "CD-56-78", "EF-90-12", "Viking", "Basal Sand", "1180.5"):
        assert name in svg
    assert svg.count("file://") >= 6            # clickable provenance links
    assert "not geographically scaled" in svg
    assert svg.strip().endswith("</svg>")


def test_cli_end_to_end(demo_tree, tmp_path, capsys):
    from wellcrawler.__main__ import main

    db = str(tmp_path / "cli.db")
    assert main(["crawl", str(demo_tree), "-o", db, "-q"]) == 0
    common = ["-c", db]

    assert main(["wells", *common]) == 0
    assert "AB-12-34" in capsys.readouterr().out

    assert main(["ask", "which wells have gas shows?", *common]) == 0
    assert "CD-56-78" in capsys.readouterr().out

    out_svg = str(tmp_path / "out.svg")
    assert main(["cross-section", "-w", "AB-12-34,CD-56-78,EF-90-12",
                 "-o", out_svg, *common]) == 0
    assert os.path.exists(out_svg)

    assert main(["sources", *common]) == 0
    listing = capsys.readouterr().out
    assert "broken wrapped log.las" in listing and "failed" in listing


def test_catalog_auto_discovery(demo_tree, tmp_path, monkeypatch, capsys):
    """After crawling a subfolder, consuming verbs find the catalog on their own."""
    from wellcrawler.__main__ import main

    workdir = tmp_path / "work"
    workdir.mkdir()
    archive = workdir / "archive"
    archive.mkdir()
    # crawl writes <root>/.wellcrawler/catalog.db; simulate by crawling a copy
    import shutil

    shutil.copytree(demo_tree, archive / "temp(14)")
    monkeypatch.chdir(workdir)
    assert main(["crawl", str(archive / "temp(14)"), "-q"]) == 0

    monkeypatch.chdir(workdir)  # consuming commands run from the parent dir
    assert main(["wells"]) == 0
    assert "AB-12-34" in capsys.readouterr().out

    assert main(["ask", "which wells have gas shows?"]) == 0
    assert "CD-56-78" in capsys.readouterr().out
