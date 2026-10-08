"""ClamAV's clamd with the EICAR-only signature database (profile clamav)."""

import subprocess

import pytest

from conftest import NAMESPACE

# The standard EICAR test file; $ is literal here.
EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest.fixture
def clamdscan(enabled_profiles):
    """Scans stdin with clamdscan in the clamav pod; returns its output and exit code."""
    if not enabled_profiles["clamav"]:
        pytest.skip("'clamav' profile is not deployed")

    def scan(content):
        result = subprocess.run(
            ["kubectl", "exec", "-i", "-n", NAMESPACE, "clamav-0", "--", "clamdscan", "--no-summary", "-"],
            input=content, capture_output=True, text=True, timeout=30, check=False,
        )  # fmt: skip
        return result.stdout.strip(), result.returncode

    return scan


def test_eicar_is_found(clamdscan):
    output, code = clamdscan(EICAR)
    assert code == 1, output
    assert "Eicar-Test-Signature" in output


def test_a_clean_file_passes(clamdscan):
    output, code = clamdscan("hello\n")
    assert code == 0, output
