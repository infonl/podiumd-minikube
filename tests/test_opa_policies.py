"""
Checks the result of the `opa-tests` Job (itest profile) - it already runs
`opa test /home/policies /home/tests` itself inside the cluster (261 cases
last verified), so this just confirms the Job actually succeeded rather
than re-running OPA locally.
"""

import pytest

from conftest import NAMESPACE, kubectl


@pytest.fixture(autouse=True)
def _skip_if_itest_disabled(enabled_profiles):
    if not enabled_profiles.get("itest"):
        pytest.skip("'itest' profile is not deployed")


def test_opa_tests_job_succeeded(pods):
    succeeded = kubectl(
        "get",
        "job",
        "opa-tests",
        "-n",
        NAMESPACE,
        "-o",
        "jsonpath={.status.succeeded}",
    ).strip()
    assert succeeded == "1", (
        "opa-tests Job did not succeed - check "
        f"`kubectl logs -n {NAMESPACE} job/opa-tests` for which policy "
        "assertions failed"
    )
