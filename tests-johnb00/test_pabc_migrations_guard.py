"""
Adapted from ../tests/test_pabc_migrations_guard.py - NOT executed against
johnb00 in this session (deliberately deselected in the pytest run: see
tests-johnb00/README.md). Two independent reasons this whole module is
skipped at collection time rather than adapted to actually run:

  1. `scripts/lib/apply-pabc-migrations.sh` (the guard script under test)
     is a podiumd-minikube-specific script that has no equivalent anywhere
     in podiumd-infra - there is nothing on johnb00's side for this test to
     exercise.
  2. Even if an equivalent existed, `test_guard_refuses_to_recreate_job_
     when_data_exists` deliberately deletes the real `pabc-migrations-1`
     Job against a real, currently-running environment - the reference
     project's own docstring calls this out as the one genuinely
     state-mutating test in the whole suite. That needs a deliberate human
     go-ahead against johnb00, not something to run as part of a routine
     suite adaptation.

The original file's logic (postgres pod exec, row-count check, --force
recovery) also assumed an in-cluster Postgres pod, which johnb00 doesn't
have either - would need the same manage.py-shell adaptation as
test_database.py even if the script existed.
"""

import pytest

pytest.skip(
    "no equivalent of scripts/lib/apply-pabc-migrations.sh exists in "
    "podiumd-infra, and this module's second test deliberately mutates "
    "real cluster state - see this file's own docstring",
    allow_module_level=True,
)
