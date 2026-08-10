#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Drops the
zac chart's own bundled otel-collector subchart resources from the
rendered manifest entirely.

Why this needs to exist: podiumd.zac.opentelemetry-collector.enabled must
be true for ZAC's own OTEL_SDK_DISABLED/OTEL_EXPORTER_OTLP_ENDPOINT env
vars to render at all (see values.yaml's own comment on that key) - but
that's the exact same key charts/zaakafhandelcomponent's own Chart.yaml
uses to decide whether to deploy this separate, unrelated bundled
otel-collector subchart too, which this project never wants running (ZAC's
traces go to templates/metrics/'s own otel-collector, or monitoring-
logging's, via the endpoint override instead - see values.yaml). A
nodeSelector/replicaCount override could suppress its pod, but not the
Deployment/Service/ConfigMap/ServiceAccount objects themselves, which
`kubectl get pods`/`tests/test_pods.py`'s blanket "every pod is
Running/Succeeded" check would otherwise flag as a permanently-Pending pod
that isn't actually a problem.

values.yaml pins its fullnameOverride to "zac-unused-otel-collector"
specifically so this filter can target it by name without needing to know
anything about which chart/values path it came from.
"""
import sys

import yaml

RESOURCE_NAME = "zac-unused-otel-collector"

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
docs = [doc for doc in docs if doc.get("metadata", {}).get("name") != RESOURCE_NAME]

yaml.safe_dump_all(docs, sys.stdout, default_flow_style=False, sort_keys=False)
