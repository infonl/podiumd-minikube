"""
Adapted from ../tests/test_productaanvraag_flow.py - skipped entirely.

That file's every check is pinned to specific fixture data seeded by
podiumd-minikube's own scripts/lib/seed-fixtures.sh (a fixed zaaktype
identificatie "zaaktype-test-1", a specific productaanvraag objecttype
UUID, a specific catalogus RSIN/domein, and a dev-only Objects API token)
- confirmed live that johnb00 has none of this fixture data seeded (it's a
podiumd-minikube-specific demo dataset, not part of any podiumd-infra
deploy step). Adapting this properly would mean seeding equivalent fixture
data on johnb00 first, which is out of scope for this test-suite
adaptation task.
"""

import pytest

pytest.skip(
    "this module's checks are pinned to podiumd-minikube's own seeded demo "
    "fixture data (scripts/lib/seed-fixtures.sh), which johnb00 doesn't "
    "have - see this file's own docstring",
    allow_module_level=True,
)
