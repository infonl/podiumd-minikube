"""lib.memory: the node's wanted memory and its warnings."""

from lib import memory


def test_wanted_is_half_the_host_unless_overridden():
    assert memory.wanted_mb(96 * 1024, None) == 48 * 1024
    assert memory.wanted_mb(96 * 1024, "20480") == 20480
