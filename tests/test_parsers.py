from __future__ import annotations

import pytest

from wellcrawler import tables
from wellcrawler.tables import TableError, parse_checkshot, parse_core_desc, parse_tops_csv
from wellcrawler.report import parse_report

CHECKSHOT = "MD_m,TVD_m,TWT_ms\n1050.0,1048.0,33.3\n1075.0,1073.0,50.0\n1100.0,1098.0,66.7\n"
CORE = (
    "DEPTH_TOP_m,DEPTH_BASE_m,LITHOLOGY,POROSITY_pct,PERM_mD,DESCRIPTION\n"
    "1180.5,1183.0,sandstone,18.4,235,fine-grained\n"
    "1188.0,1191.5,sandstone,21.7,410,coarse\n"
)
TOPS = "Formation,Depth_m,Unit\nViking,1095.0,m\nBasal Sand,1150.25,m\n"


def test_checkshot_points():
    cs = parse_checkshot(CHECKSHOT)
    assert len(cs.points) == 3
    assert cs.points[0] == (1050.0, 1048.0, 33.3)


def test_checkshot_missing_time_column():
    with pytest.raises(TableError, match="checkshot"):
        parse_checkshot("MD_m,TVD_m\n1050.0,1048.0\n")


def test_core_intervals():
    intervals = parse_core_desc(CORE)
    assert len(intervals) == 2
    assert intervals[0].lithology == "sandstone"
    assert intervals[0].porosity == 18.4
    assert intervals[1].top_md == 1188.0


def test_tops():
    tops = parse_tops_csv(TOPS)
    assert [(t.formation, t.depth_md) for t in tops] == [("Viking", 1095.0), ("Basal Sand", 1150.25)]


REPORT = (
    "COMPOSITE WELL RECORD\n"
    "WELL: CD-56-78\n"
    "LSID: 14-22-056-25W4\n"
    "\n"
    "FORMATION TOPS\n"
    "Viking .......................... 1122.0 m\n"
    "Base of Viking .................. 1156.5 m\n"
    "Basal Sand ...................... 1180.5 m\n"
    "\f"
    "DRILLING AND SHOWS REPORT\n"
    "Mud weight 1150 kg/m3 at 1100 m.\n"
    "1180.5-1183.0 m: gas cut mud with moderate fluorescence.\n"
    "1188.0-1189.5 m: oil stain and residual bitumen in ditch samples.\n"
    "1210.0 m: connection gas peak of 12 percent.\n"
    "Hydrocarbon shows summarized on page 2.\n"
)


def test_report_well_hint():
    assert parse_report(REPORT).well_hint == "CD-56-78"


def test_report_tops_with_page_provenance():
    tops = parse_report(REPORT).tops
    assert [(t.formation, t.depth_md) for t in tops] == [
        ("Viking", 1122.0), ("Base of Viking", 1156.5), ("Basal Sand", 1180.5),
    ]
    assert all(t.page == 1 for t in tops)
    assert all(t.line_no for t in tops)
    assert tops[0].excerpt.startswith("Viking")


def test_report_shows_fluids_and_pages():
    shows = parse_report(REPORT).shows
    fluids = [(s.fluid, s.top_md, s.base_md, s.page) for s in shows]
    assert ("gas", 1180.5, 1183.0, 2) in fluids
    assert ("oil", 1188.0, 1189.5, 2) in fluids
    assert ("gas", 1210.0, 1210.0, 2) in fluids


def test_report_rejects_page_numbers_as_depths():
    """The 'page 2' line must not become a show at 2 m."""
    shows = parse_report(REPORT).shows
    assert all(s.top_md >= 100 for s in shows)


def test_report_mud_weight_observation():
    observations = parse_report(REPORT).observations
    assert observations and observations[0].kind == "mud_weight"
    assert observations[0].depth_md == 1100.0
    assert observations[0].page == 2


def test_report_no_false_tops_from_show_lines():
    tops = parse_report(REPORT).tops
    assert not any("mud" in t.formation.lower() for t in tops)
