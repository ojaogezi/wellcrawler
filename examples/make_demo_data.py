"""Generate a deterministic, messy ``temp(14)`` demo archive.

Run from the repo root:

    python examples/make_demo_data.py [output-dir]

The tree deliberately reproduces the pathologies of a real shared drive:
generic folder names, misleading/misnamed files (a LAS saved as .txt, a
report that is the only home of the formation tops), versioning junk in
filenames, a broken wrapped LAS, and outright junk files.
"""
from __future__ import annotations

import math
import os
import sys


def build(root: str) -> None:
    _las_ab(os.path.join(root, "FINAL - do not delete",
                         "12-34-056-25W4_AB-12-34_las_FINAL2.las"))
    _tops_ef(os.path.join(root, "FINAL - do not delete", "EF-90-12_tops_export.csv"))
    _las_cd(os.path.join(root, "logs from contractor (old)", "14-22-056-25W4 run1.txt"))
    _broken_las(os.path.join(root, "logs from contractor (old)", "broken wrapped log.las"))
    _checkshot_ab(os.path.join(root, "checkshots exported by intern", "AB-12-34_chk_new.csv"))
    _core_ab(os.path.join(root, "core", "AB-12-34 core descriptions vFINAL.csv"))
    _core_cd(os.path.join(root, "core", "CD-56-78 core desc v2.csv"))
    _report_cd(os.path.join(root, "scans", "legacy report CD-56-78.txt"))
    _junk(os.path.join(root, "notes_about_lunch.txt"))
    _binary_junk(os.path.join(root, "Thumbs.db"))


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _las(well: str, uwi: str, field: str, top: float, base: float,
         curves: list, sand_top: float, sand_base: float) -> str:
    """Build a small LAS 2.0 with a synthetic sand response."""
    lines = [
        "~Version Information",
        "VERS. 2.0 : CWLS log ASCII standard - version 2.0",
        "WRAP. NO : one line per depth step",
        "~Well Information",
        f"#Generated demo data for well {well}",
        f"WELL. {well} : well name",
        f"UWI . {uwi} : unique well id",
        f"FLD . {field} : field",
        "SRVC. Fictitious Logging Ltd. : service company",
        "DATE. 1998-07-14 : log date",
        "NULL. -999.25 : null value",
        "~Curve Information",
        "DEPT.M                 : depth",
    ]
    for mnemonic, unit, desc in curves:
        lines.append(f"{mnemonic}.{unit:<22}: {desc}")
    lines.append("~Ascii")
    n = int((base - top) / 0.5)
    for i in range(n + 1):
        depth = top + 0.5 * i
        in_sand = sand_top <= depth <= sand_base
        row = [f"{depth:8.1f}"]
        for j, (mnemonic, unit, _desc) in enumerate(curves):
            phase = i * (0.31 + 0.07 * j)
            if mnemonic.upper().startswith("GR"):
                value = (46.0 + 9.0 * math.sin(phase)) if in_sand else (92.0 + 14.0 * math.sin(phase))
            elif mnemonic.upper().startswith("RHOB"):
                value = (2.12 + 0.02 * math.sin(phase)) if in_sand else (2.55 + 0.02 * math.sin(phase))
            elif mnemonic.upper().startswith("DT"):
                value = (104.0 + 6.0 * math.sin(phase)) if in_sand else (74.0 + 5.0 * math.sin(phase))
            elif mnemonic.upper().startswith("NPHI"):
                value = (0.26 + 0.01 * math.sin(phase)) if in_sand else (0.12 + 0.01 * math.sin(phase))
            else:
                value = -999.25
            if depth > base - 2.0 and j == 0:  # a couple of nulls for realism
                value = None
            row.append(f"{value:8.3f}" if value is not None else "  -999.25")
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"


def _las_ab(path: str) -> None:
    _write(path, _las(
        well="AB-12-34", uwi="100/12-34-056-25W4/0", field="Horseshoe",
        top=1050.0, base=1350.0,
        curves=[("GR", "gAPI", "gamma ray"), ("RHOB", "g/cm3", "bulk density")],
        sand_top=1180.5, sand_base=1215.0,
    ))


def _las_cd(path: str) -> None:
    _write(path, _las(
        well="CD-56-78", uwi="100/14-22-056-25W4/0", field="Horseshoe",
        top=1080.0, base=1300.0,
        curves=[("DT", "us/m", "sonic"), ("NPHI", "v/v", "neutron porosity")],
        sand_top=1180.5, sand_base=1189.5,
    ))


def _broken_las(path: str) -> None:
    _write(path, "~Version Information\nVERS. 1.2\nWRAP. YES\n~W\n~C\n~A\n")


def _checkshot_ab(path: str) -> None:
    rows = ["MD_m,TVD_m,TWT_ms"]
    for i in range(13):
        md = 1050.0 + 25.0 * i
        tvd = md - 2.0
        twt = 2.0 * (md - 1000.0) / 3.0  # ~3000 m/s, two-way, ms
        rows.append(f"{md:.1f},{tvd:.1f},{twt:.1f}")
    _write(path, "\n".join(rows) + "\n")


def _core_ab(path: str) -> None:
    _write(path, "\n".join([
        "DEPTH_TOP_m,DEPTH_BASE_m,LITHOLOGY,POROSITY_pct,PERM_mD,DESCRIPTION",
        "1180.5,1183.0,sandstone,18.4,235,fine-grained, well sorted, faint oil odor",
        "1183.0,1188.0,interbedded sand and shale,12.1,45,rippled, carbonaceous drapes",
        "1188.0,1191.5,sandstone,21.7,410,coarse, slightly glauconitic",
        "1191.5,1196.0,shale,4.2,0.01,silty, dark grey, fissile",
        "1196.0,1201.0,dolomite,9.8,12,finely crystalline, vuggy",
    ]) + "\n")


def _core_cd(path: str) -> None:
    _write(path, "\n".join([
        "DEPTH_TOP_m,DEPTH_BASE_m,LITHOLOGY,POROSITY_pct,PERM_mD,DESCRIPTION",
        "1180.5,1182.0,sandstone,15.9,180,fluorescence under UV, poor cut",
        "1182.0,1186.5,siltstone,8.3,5,bioturbated",
        "1186.5,1189.5,sandstone,19.2,320,oil stained, dead oil in pores",
        "1189.5,1194.0,limestone,6.5,8,skeletal wackestone",
    ]) + "\n")


def _tops_ef(path: str) -> None:
    _write(path, "\n".join([
        "Formation,Depth_m,Unit",
        "Viking,1095.0,m",
        "Base of Viking,1128.5,m",
        "Basal Sand,1150.25,m",
        "Granite Wash,1184.0,m",
    ]) + "\n")


def _report_cd(path: str) -> None:
    _write(path, (
        "COMPOSITE WELL RECORD\n"
        "WELL: CD-56-78\n"
        "LSID: 14-22-056-25W4\n"
        "Operator: Fictitious Exploration Ltd.\n"
        "KB elevation 682.0 m; ground level 679.5 m; spud 1997-11-03.\n"
        "\n"
        "FORMATION TOPS (picked by hand from prints; see page 2 for shows)\n"
        "Viking .......................... 1122.0 m\n"
        "Base of Viking .................. 1156.5 m\n"
        "Basal Sand ...................... 1180.5 m\n"
        "Granite Wash .................... 1215.0 m\n"
        "\f"
        "DRILLING AND SHOWS REPORT\n"
        "Mud weight 1150 kg/m3 at 1100 m; raised to 1180 kg/m3 by total depth 1240 m.\n"
        "1180.5-1183.0 m: gas cut mud with moderate even-cut fluorescence and a\n"
        "rate-of-penetration break at the Basal Sand.\n"
        "1188.0-1189.5 m: oil stain and residual bitumen in ditch samples.\n"
        "1210.0 m: connection gas peak of 12 percent total gas.\n"
        "\f"
        "CORE AND SAMPLES\n"
        "No conventional core recovered; ditch samples at 3 m intervals.\n"
        "Hydrocarbon shows summarized on page 2.\n"
    ))


def _junk(path: str) -> None:
    _write(path, (
        "lunch order - ping me\n"
        "- FE: paella (no shrimp)\n"
        "- geo: the usual sandwich\n"
        "also does anyone know whose temp(14) folder this is???\n"
    ))


def _binary_junk(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00\x01\x02JFIF-ish junk\xff\xfe" * 40)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("examples", "temp(14)")
    build(out)
    count = sum(len(files) for _d, _sd, files in os.walk(out))
    print(f"wrote {count} files under {out}")
