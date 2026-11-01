from __future__ import annotations

from wellcrawler.registry import (
    Registry, canonical_uwi, find_uwi, name_from_filename, normalize,
)


def test_normalize_strips_separators():
    assert normalize("AB-12_34/x") == "AB1234X"
    assert normalize("ab 12.34") == "AB1234"


def test_find_uwi_full_and_partial():
    assert find_uwi("100/12-34-056-25W4/0") == "100/12-34-056-25W4/0"
    assert find_uwi("see well 14-22-056-25W4 please") == "14-22-056-25W4"
    assert find_uwi("no identifier here") is None


def test_canonical_uwi_merges_variants():
    assert canonical_uwi("100/14-22-056-25W4/0") == "14-22-056-25W4"
    assert canonical_uwi("14-22-056-25W4") == "14-22-056-25W4"


def test_name_from_filename_drops_noise():
    assert name_from_filename("AB-12-34_las_FINAL2.las") == "AB-12-34"
    assert name_from_filename("legacy report CD-56-78.txt") == "CD-56-78"
    assert name_from_filename("AB-12-34 core descriptions vFINAL.csv") == "AB-12-34"


def test_name_from_filename_uwi_wins():
    assert name_from_filename("12-34-056-25W4_AB-12-34_las_FINAL2.las") == "12-34-056-25W4"


def test_name_from_filename_never_empty():
    assert name_from_filename("export.csv")
    assert name_from_filename("FINAL copy 2.las")


def test_registry_merges_alias_spellings():
    reg = Registry()
    w1 = reg.register(["AB-12-34"], priority=Registry.PRIORITY_LAS)
    w2 = reg.register(["ab_12_34", "AB 12 34"], priority=Registry.PRIORITY_FILENAME)
    assert w1 is w2
    assert len(reg.wells()) == 1


def test_registry_merges_on_uwi():
    reg = Registry()
    w1 = reg.register(["CD-56-78"], uwi="100/14-22-056-25W4/0", priority=8)
    w2 = reg.register(["14-22-056-25W4"], uwi="14-22-056-25W4", priority=1)
    assert w1 is w2
    assert w1.uwi == "100/14-22-056-25W4/0"
    assert "14-22-056-25W4" in w1.aliases


def test_display_prefers_las_header():
    reg = Registry()
    reg.register(["14-22-056-25W4"], priority=Registry.PRIORITY_FILENAME)
    reg.register(["CD-56-78", "14-22-056-25W4"],
                 uwi="100/14-22-056-25W4/0", priority=Registry.PRIORITY_LAS)
    reg.register(["CD-56-78"], priority=Registry.PRIORITY_TABLE)
    assert len(reg.wells()) == 1
    well = reg.wells()[0]
    well.finalize_display()
    assert well.display_name == "CD-56-78"
