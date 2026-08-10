#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Drops any
resource annotated `helm.sh/hook: test` from the rendered manifest entirely.

Why this needs to exist: deploy.sh applies via plain `kubectl apply`, never
`helm install`/`helm test` (see deploy.sh's own header for why - no real
Helm release ever exists for hooks to attach to). A test hook applied as a
plain resource isn't a no-op, though - it's a Pod that actually runs once,
then sits in a terminal state (Completed or, more often, Error - no
Service/DNS-ready guarantees at the point `kubectl apply` happens to create
it) forever, since nothing ever re-triggers or deletes it (confirmed live:
monitoring-logging's own grafana subchart bundles exactly one of these -
"<release>-grafana-test" - which fails tests/test_pods.py's "every pod is
Running/Succeeded" check the moment monitoringLogging.enabled=true).
Pre-install/post-install hooks are deliberately left alone here - unlike
test hooks, at least some of those (e.g. kube-prometheus-stack's own
admission-webhook cert-generation Jobs) need to actually run for the rest
of the chart to work, hook ordering guarantees or not.
"""
import sys

import yaml

def is_test_hook(doc):
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    return "test" in (annotations.get("helm.sh/hook") or "")


docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
docs = [doc for doc in docs if not is_test_hook(doc)]

yaml.safe_dump_all(docs, sys.stdout, default_flow_style=False, sort_keys=False)
