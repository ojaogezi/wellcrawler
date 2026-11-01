from __future__ import annotations

import pytest

from wellcrawler import las
from wellcrawler.las import LasError, parse

BASIC = """~Version Information
VERS. 2.0 : CWLS log ASCII standard
WRAP. NO : one line per depth step
~Well Information
# a comment line
WELL. AB-12-34 : well name
UWI . 100/12-34-056-25W4/0 : unique well id
FLD . Horseshoe : field
NULL. -999.25 : null value
STRT.M 1000.0 : start depth
~Curve Information
DEPT.M                : depth
GR.gAPI               : gamma ray
RHOB.g/cm3            : density
~Ascii
1000.0 90.0 2.55
1000.5 88.0 2.54
1001.0 -999.25 2.55
1001.5 85.0 -999.25
"""


def test_header_fields():
    parsed = parse(BASIC)
    assert parsed.well == "AB-12-34"
    assert parsed.uwi == "100/12-34-056-25W4/0"
    assert parsed.field_name == "Horseshoe"
    assert parsed.null == -999.25
    assert parsed.params["STRT"] == "1000.0"


def test_curves_and_nulls():
    parsed = parse(BASIC)
    assert [c.mnemonic for c in parsed.curves] == ["DEPT", "GR", "RHOB"]
    assert parsed.columns["GR"] == [90.0, 88.0, None, 85.0]
    assert parsed.columns["RHOB"] == [2.55, 2.54, 2.55, None]


def test_samples_pairs_drop_nulls():
    parsed = parse(BASIC)
    depths, values = parsed.samples("GR")
    assert depths == [1000.0, 1000.5, 1001.5]
    assert values == [90.0, 88.0, 85.0]


def test_depth_mnemonic_detection():
    parsed = parse(BASIC)
    assert parsed.depth_mnemonic() == "DEPT"


def test_missing_ascii_raises():
    with pytest.raises(LasError, match="~A"):
        parse("~V\nVERS. 2.0\n~W\nWELL. X\n~C\nDEPT.M : d\n")


def test_wrapped_las_raises():
    with pytest.raises(LasError, match="WRAP"):
        parse("~V\nVERS. 1.2\nWRAP. YES\n~C\nDEPT.M : d\n~A\n1000 90\n")


def test_duplicate_mnemonics_deduped():
    text = BASIC.replace("RHOB.g/cm3            : density", "RHOB.g/cm3 : density\nRHOB.g/cm3 : density again")
    parsed = parse(text)
    assert [c.mnemonic for c in parsed.curves] == ["DEPT", "GR", "RHOB", "RHOB_2"]


def test_misaligned_data_line_dropped():
    text = BASIC + "1002.0 80.0 2.50 1.0\n"
    parsed = parse(text)
    assert len(parsed.columns["DEPT"]) == 4
    assert any("misaligned" in w for w in parsed.warnings)
