"""podiumd_minikube.objects: YAML text of data."""

from podiumd_minikube.objects import serialize_yaml
from podiumd_minikube.objects import serialize_yaml_all


def test_a_stream_keeps_key_order_in_block_style():
    assert serialize_yaml_all([{"b": 1, "a": [1, {"c": "x"}]}]) == "b: 1\na:\n- 1\n- c: x\n"


def test_aliases_do_not_cross_documents():
    shared = {"k": "v"}
    assert serialize_yaml_all([{"x": shared}, {"y": shared}]) == "x:\n  k: v\n---\ny:\n  k: v\n"


def test_one_document_sorts_its_keys_unless_asked_not_to():
    assert serialize_yaml({"b": 1, "a": 2}) == "a: 2\nb: 1\n"
    assert serialize_yaml({"b": 1, "a": 2}, sort_keys=False) == "b: 1\na: 2\n"
