"""lib.disk: the free-space verdict."""

from lib import disk

GIB = disk.GIB


def test_verdict_fails_below_the_minimum_and_warns_when_tight():
    need = disk.Need(fail_gib=3, warn_gib=10)
    assert disk.verdict(100 * GIB, 2 * GIB, need)[0] == "fail"
    assert disk.verdict(100 * GIB, 5 * GIB, need)[0] == "warn"
    assert disk.verdict(100 * GIB, 12 * GIB, need)[0] == "warn"  # 88% used
    assert disk.verdict(100 * GIB, 24 * GIB, need)[0] == "ok"
