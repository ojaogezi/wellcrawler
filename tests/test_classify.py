from __future__ import annotations

from wellcrawler import classify


def _write(tmp_path, name, text, binary=False):
    path = tmp_path / name
    if binary:
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return str(path)


LAS_TEXT = """~Version Information
VERS. 2.0 : CWLS log ASCII standard
WRAP. NO : one line per depth step
~Well Information
WELL. AB-12-34 : well name
~Curve Information
DEPT.M  : depth
GR.gAPI : gamma ray
~Ascii
1000.0 90.0
1000.5 88.0
"""


def test_las_by_content(tmp_path):
    result = classify.classify(_write(tmp_path, "mystery_file.dat", LAS_TEXT))
    assert result.kind == classify.LAS
    assert result.is_data


def test_las_misnamed_extension_still_found(tmp_path):
    result = classify.classify(_write(tmp_path, "run notes final.txt", LAS_TEXT))
    assert result.kind == classify.LAS


def test_checkshot_csv(tmp_path):
    text = "MD_m,TVD_m,TWT_ms\n1050.0,1048.0,33.3\n1075.0,1073.0,50.0\n"
    result = classify.classify(_write(tmp_path, "export.csv", text))
    assert result.kind == classify.CHECKSHOT


def test_tops_csv(tmp_path):
    text = "Formation,Depth_m\nViking,1095.0\nBasal Sand,1150.0\n"
    result = classify.classify(_write(tmp_path, "export2.csv", text))
    assert result.kind == classify.TOPS_CSV


def test_core_csv(tmp_path):
    text = (
        "DEPTH_TOP_m,DEPTH_BASE_m,LITHOLOGY,POROSITY_pct\n"
        "1180.5,1183.0,sandstone,18.4\n"
    )
    result = classify.classify(_write(tmp_path, "core thing.csv", text))
    assert result.kind == classify.CORE_DESC


def test_paginated_report(tmp_path):
    text = (
        "WELL: CD-56-78\n"
        "Viking ................ 1122.0 m\n"
        "\f"
        "1180.5-1183.0 m: gas cut mud, fluorescence.\n"
        "Mud weight 1150 kg/m3 at 1100 m.\n"
    )
    result = classify.classify(_write(tmp_path, "scan.txt", text))
    assert result.kind == classify.REPORT


def test_plain_junk_is_unknown(tmp_path):
    result = classify.classify(_write(tmp_path, "notes.txt", "lunch order, ping me\n"))
    assert result.kind == classify.UNKNOWN
    assert not result.is_data


def test_binary_is_unknown(tmp_path):
    result = classify.classify(_write(tmp_path, "Thumbs.db", b"\x00\x01\xff" * 64, binary=True))
    assert result.kind == classify.UNKNOWN


def test_empty_file_is_unknown(tmp_path):
    result = classify.classify(_write(tmp_path, "empty.las", ""))
    assert result.kind == classify.UNKNOWN
