---
name: reuse-existing-logic
description: "One concept, one place in podiumd-minikube; check the shared helpers in src/podiumd_minikube, values.yaml and tests/conftest.py before writing new code"
metadata:
  type: feedback
---

# One concept, one place

Adapted (2026-10-07) from the user's rule for podiumd-tests
(`infonl/podiumd-tests/.claude/memory/reuse-existing-logic.md`), itself from
helm-charts `charts/podiumd/bin/`. Applies to all of podiumd-minikube:
`values.yaml`, `templates/`, `src/podiumd_minikube/`, `unit_tests/` and `tests/`. Code
gets better when it gets smaller while keeping the same or more
functionality. A change that adds lines for behaviour that already exists
elsewhere is a regression, even when the tests pass.

**Why:** the user wants a small, consistent code base; copies drift (found
live: the tunnel script's own host list went stale, which is why
`src/podiumd_minikube/hosts.py` exists).

**How to apply:**

## Before writing new code

1. Name the concept, not the function: "the hosts of the Ingresses", "the
   rendered manifest", "is the profile enabled", "the cluster is minikube".
2. List the low-level calls the new code would make (`helm template`,
   `kubectl get`, a regex over `values.yaml`, `requests.get`, ...) and grep
   every caller of each. A caller that already computes the same thing is the
   place to extend; do not write a second one.
3. Use the shared entry point when one exists:

   | Concept | Use |
   | --- | --- |
   | starting any external process (kubectl, helm, minikube, docker) | `podiumd_minikube.process` (`run`, `output`, `succeeds`, `spawn`; errors are `ProcessError`/`UserError`) |
   | a command | a function in `cli.py`, listed in `COMMANDS`; logic in its own module. `bin/` holds only symlinks to `bin/.stub` |
   | refuse to run against a non-minikube kubectl context | `kube.require_minikube_context` |
   | kubectl calls, the edge IP, a pod | `podiumd_minikube.kube` (`kubectl`, `kubectl_shown`, `get_json`, `exists`, `edge_ip`, `first_pod`) |
   | waiting for a condition | `polling.wait_until` (never a bare sleep) |
   | node/container memory and CPU, running profiles, problem pods | `podiumd_minikube.status` (also used by `tests/test_memory.py` and `tests/conftest.py`) |
   | changing the shared cluster | `lock.holding`/`lock.held` (commands), `podiumd-minikube cluster-lock` (by hand) |
   | the rendered manifest, with all local fixups | `deploy.Options.render`; a new fixup is one more step in `manifests.fix_up` |
   | YAML sections that may be null, a resource's name | `manifests.section`, `manifests.name_of` |
   | the `*.local` hosts of all Ingresses | `podiumd_minikube.hosts` |
   | podiumd chart version and `charts/*.tgz` sync | `podiumd_minikube.dependency` (`selection`, `write`, `sync`) |
   | a single setting from `values.yaml` | `podiumd_minikube.values` |
   | facts from the vendored tarballs (objecten shape) | `podiumd_minikube.chart` |
   | the monitoring-logging CRDs | `crds.apply_monitoring_logging_crds` |
   | unit-test fakes for processes and tarballs | fixture `fake_run`, `make_tgz` in `unit_tests/conftest.py` |
   | PV/PVC pre-provisioning for an app | `templates/storage-hooks.yaml` |
   | a database and user for an app | the init SQL in `templates/postgres` |
   | Mailpit | the existing `templates/<name>` Deployment; never a subchart's bundled copy |
   | common labels | `podiumd-minikube.labels` in `templates/_helpers.tpl` |
   | an optional profile switch | `podiumd.<name>.enabled` in `values.yaml`, set by `--full` (`chart.FULL_PROFILE_SETS`) |
   | kubectl from tests | `kubectl` in `tests/conftest.py` |
   | edge address, pod list, enabled profiles | fixtures `edge_ip`, `pods`, `enabled_profiles` |
   | HTTP to an ingress host from tests | `host_url` |
   | the CNI (Cilium), its values and start flags | `podiumd_minikube.cni` |
   | the edge's routes, Gateway and certificate | `podiumd_minikube.gateway` (routes come from the rendered Ingresses) |

## While changing code

- Logic lives in `src/podiumd_minikube/` with unit tests in `unit_tests/`;
  `cli.py` only parses arguments and calls it.
- A near-copy of existing code is merged in the same change, including copies
  that differ only in a detail.
- A split or move of code also merges the pieces that do the same thing;
  never leave a wrapper that only forwards. One exception, at the user's
  request (2026-10-12): `bin/<command>` keeps the old command names working,
  as symlinks to the single `bin/.stub`; `unit_tests/test_cli.py` checks they
  match `cli.COMMANDS`.
- A value that several places need (a host, a URL, a port, a credential)
  comes from one place: `values.yaml`, or one generated list.

## Before committing

- `./run_python_checks` passes, including jscpd (copied code) and vulture.
- New comments, help texts and messages are reviewed against
  [[comments-and-help-texts]].
- Review the diff for "where else is this computed?": for each new function,
  script or template, search for the same concept by its low-level calls
  (step 2 above), preferably with a review subagent that has not seen the
  change being written.
- The commit message names the existing helpers that were checked, and which
  one was extended or why none fit.
