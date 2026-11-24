---
name: env-ledger
description: "Book minikube and kees00 in the env ledger before any deploy, reset, chart test, test run beyond smoke, or disruptive step; log every podiumd deploy"
metadata:
  type: feedback
---

# Env ledger

The user's rule (2026-11-18, restated 2026-11-24) for every agent: book a shared
test environment before using it. The ledger is podiumd-local-review's
`.claude/skills/env-ledger/env-ledger.py`; read its `SKILL.md` for the
commands. `minikube` is a local file per user and host; `kees00` is ConfigMap
`agent-env-ledger`, with a local mirror while kees00 is offline.

**Why:** resets, chart tests and disruptive runs of podiumd-tests,
podiumd-minikube, podiumd-infra and helm-charts landed on each other.

**How to apply:**

1. Before any deploy, reset, chart test, test run beyond smoke or single
   reruns, or disruptive step on minikube or kees00: `$E check ENV --for ...`,
   then `$E book ENV --agent podiumd-minikube --what "..." --for ...`.
   A deploy is `--disruptive`; a reset is `--disruptive --resets`.
2. Book as soon as the work is planned, not when it starts. When another
   agent deploys or rebuilds on your behalf, book your slot after theirs in
   the same step.
3. During long work, `heartbeat` at least every 20 minutes; afterwards
   `done`. Every podiumd Helm deploy: `$E log ENV --kind deploy --target
   helm/podiumd --summary "rev N #PR"` (`rev N FAILED` on failure).
4. On a conflict, message the agent that holds the booking. Never `--force`
   unless the user says so.
5. The locks stay: `podiumd-minikube cluster-lock` (`.minikube-lock`) and the
   kees00 lease `podiumd-tests-lock`.

Related: [[python-checks]], [[laptop-resources]].
