"""lib.seed: the fixture-only objecttype left out of the demo data."""

from lib import seed


def test_without_objecttype_drops_the_type_and_what_points_at_it():
    rows = [
        {"model": "core.objecttype", "pk": 1, "fields": {"uuid": "keep"}},
        {"model": "core.objecttype", "pk": 4, "fields": {"uuid": "drop"}},
        {"model": "core.objecttypeversion", "pk": 4, "fields": {"object_type": 4}},
        {"model": "core.object", "pk": 30, "fields": {"object_type": 1}},
        {"model": "core.object", "pk": 31, "fields": {"object_type": 4}},
        {"model": "core.objectrecord", "pk": 50, "fields": {"object": 31, "_object_type": None}},
        {"model": "core.objectrecord", "pk": 51, "fields": {"object": 30}},
        {"model": "token.permission", "pk": 3, "fields": {"object_type": 4}},
        {"model": "token.tokenauth", "pk": 1, "fields": {"identifier": "openzaak"}},
    ]
    kept = [(row["model"], row["pk"]) for row in seed.without_objecttype(rows, "drop")]
    assert kept == [("core.objecttype", 1), ("core.object", 30), ("core.objectrecord", 51), ("token.tokenauth", 1)]
