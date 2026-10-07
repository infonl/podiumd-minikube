"""lib.memory: the node's wanted memory and its warnings."""

from lib import memory


def test_wanted_is_half_the_host_unless_overridden():
    assert memory.wanted_mb(96 * 1024, None) == 48 * 1024
    assert memory.wanted_mb(96 * 1024, "20480") == 20480


def test_shortfalls_for_a_small_node_and_a_full_deploy():
    assert memory.shortfalls(48 * 1024, 48 * 1024, full=True) == []
    assert len(memory.shortfalls(16 * 1024, 48 * 1024, full=False)) == 1
    assert len(memory.shortfalls(16 * 1024, 48 * 1024, full=True)) == 2
    assert memory.shortfalls(20 * 1024, 16 * 1024, full=False) == []
