#!/usr/bin/env bash
# Thin wrapper around `deploy.sh --full` for one specific non-default
# profile combination (built up interactively, one `--set` at a time, in a
# single session): every `--full` profile except metrics and wiremock's
# extra mappings, plus openzaak's and openklant's Celery workers (which
# `--full` itself doesn't turn on).
#
# Exists because deploy.sh always renders from scratch - re-running it
# with a *different* (or empty) set of `--set` flags than whatever's
# currently deployed prunes the difference right back out (see deploy.sh's
# own header). Hand-typing this exact flag list every time is how that
# happens by accident; this script is the durable record of "this
# cluster's intended state" instead.
#
# Layered on top of `--full` rather than re-deriving objecten/opennotificaties/
# openarchiefbeheer/openformulieren by hand: that also reuses --full's own
# objecten/objecttypen classic-vs-merged shape detection (see
# detect-objecten-shape.sh) for free, so this keeps working across a
# set-podiumd-version.sh switch instead of silently drifting from it.
#
# - metrics: left off - this setup was built for testing the actual apps,
#   not their observability stack.
# - wiremock's extra mappings: left off - nothing here exercises the
#   smartdocuments/kvk/bag mocks.
# - openzaak/openklant's workers: off by default because compose itself
#   never runs them outside specific profiles (see values.yaml's own
#   `podiumd.openzaak.worker`/`podiumd.openklant.worker` comments) - turned
#   on here because opennotificaties needs openzaak's worker to actually
#   publish notifications, and openklant's was requested alongside it.
#
# Usage:
#   ./scripts/deploy-extended.sh                  # this profile combination
#   ./scripts/deploy-extended.sh --set some.other=value   # extra flags, forwarded to deploy.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "${CHART_DIR}/scripts/deploy.sh" --full \
  --set metrics.enabled=false \
  --set wiremock.enabled=false \
  --set podiumd.openzaak.worker.replicaCount=1 --set podiumd.openklant.worker.replicaCount=1 \
  "$@"
