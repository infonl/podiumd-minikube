#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Drops the
pabc-migrations Job from the rendered manifest entirely.

Why this needs to exist: deploy.sh's main "apply everything" step is one
unguarded `kubectl apply` over the full render - fine for every other
resource (Jobs are immutable, so re-applying an *existing* one is already
a safe no-op), but genuinely dangerous for this one specifically: its
container clears PABC's database before reloading its seed dataset every
time it actually runs. If this Job were ever missing (deleted, or simply
never created yet) when that unfiltered apply ran, plain `kubectl apply`
would silently create + run it with no guard at all - even against a
database that already has real data added since the last successful run.

`scripts/apply-pabc-migrations.sh` is the one place allowed to (re)create
this Job - it checks first and refuses without `--force`. `deploy.sh` calls
it as its own explicit, later step instead of relying on the general
manifest apply to create it implicitly. See that script's own header for
the full reasoning.
"""
import sys

import yaml

JOB_NAME = "pabc-migrations-1"

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
docs = [
    doc
    for doc in docs
    if not (doc.get("kind") == "Job" and doc.get("metadata", {}).get("name") == JOB_NAME)
]

yaml.safe_dump_all(docs, sys.stdout, default_flow_style=False, sort_keys=False)
