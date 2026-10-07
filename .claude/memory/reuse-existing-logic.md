---
name: reuse-existing-logic
description: "One concept, one place in podiumd-minikube; check the shared helpers in scripts/lib, values.yaml and tests/conftest.py before writing new code"
metadata:
  type: feedback
---

# One concept, one place

Adapted (2026-10-07) from the user's rule for podiumd-tests
(`infonl/podiumd-tests/.claude/memory/reuse-existing-logic.md`), itself from
helm-charts `charts/podiumd/bin/`. Applies to all of podiumd-minikube:
`values.yaml`, `templates/`, `scripts/`, `scripts/lib/` and `tests/`. Code
gets better when it gets smaller while keeping the same or more
functionality. A change that adds lines for behaviour that already exists
elsewhere is a regression, even when the tests pass.

**Why:** the user wants a small, consistent code base; copies drift (found
live: the tunnel script's own host list went stale, which is why
`scripts/lib/hosts.py` exists).

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
   | starting any external process (kubectl, helm, minikube, docker) | `lib.process` (`run`, `output`, `succeeds`, `spawn`; errors are `ProcessError`/`UserError`) |
   | a script's entry point | `process.main(...)` in an extensionless `scripts/<name>`; logic in `scripts/lib/` |
   | refuse to run against a non-minikube kubectl context | `kube.require_minikube_context` |
   | kubectl calls, the edge IP, a pod, a Django shell | `lib.kube` (`kubectl`, `kubectl_shown`, `get_json`, `exists`, `edge_ip`, `first_pod`, `django_shell`) |
   | waiting for a condition | `polling.wait_until` (never a bare sleep) |
   | the rendered manifest, with all local fixups | `deploy.Options.render`; a new fixup is one more step in `manifests.fix_up` |
   | YAML sections that may be null, a resource's name | `manifests.section`, `manifests.name_of` |
   | the `*.local` hosts of all Ingresses | `lib.hosts` |
   | podiumd chart version and `charts/*.tgz` sync | `lib.dependency` (`selection`, `write`, `sync`) |
   | a flag in a top-level `values.yaml` block | `lib.values` (`flag`, `set_flag`, `monitoring_logging_enabled`, `zac_experimental_pkce`) |
   | facts from the vendored tarballs (objecten shape, ZAC PKCE support) | `lib.chart` |
   | the ZAC PKCE live realm sync | `keycloak.sync_zac_pkce` |
   | (re)creating the pabc-migrations Job | `pabc.apply_migrations`; never an ad-hoc delete + apply |
   | the monitoring-logging CRDs | `crds.apply_monitoring_logging_crds` |
   | demo/fixture data in a running app | `seed.seed_fixtures` |
   | unit-test fakes for processes and tarballs | fixture `fake_run`, `make_tgz` in `scripts/tests/conftest.py` |
   | PV/PVC pre-provisioning for an app | `templates/storage-hooks.yaml` |
   | a database and user for an app | the init SQL in `templates/postgres` |
   | Redis, Mailpit, Keycloak, Solr, RabbitMQ | the existing `templates/<name>` Deployment; never a subchart's bundled copy |
   | common labels | `podiumd-minikube.labels` in `templates/_helpers.tpl` |
   | an optional profile switch | `<name>.enabled` + `podiumd.<name>.enabled` in `values.yaml`, set by `--full` in `lib.deploy.options` |
   | kubectl from tests | `kubectl` in `tests/conftest.py` |
   | edge address, pod list, enabled profiles | fixtures `edge_ip`, `pods`, `enabled_profiles` |
   | HTTP to an ingress host from tests | `host_url` |
   | the edge's routes, Gateway and certificate | `lib.gateway` (routes come from the rendered Ingresses) |

## While changing code

- Logic lives in `scripts/lib/` with unit tests in `scripts/tests/`; the
  extensionless scripts only parse arguments and call it.
- A near-copy of existing code is merged in the same change, including copies
  that differ only in a detail.
- A split or move of code also merges the pieces that do the same thing;
  never leave a wrapper that only forwards.
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
