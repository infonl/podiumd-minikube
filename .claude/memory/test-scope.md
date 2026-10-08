# Test scope: this project's code, not what runs inside minikube

The tests here (`tests/` live, `scripts/tests/` unit) cover the code of this
project: `scripts/`, `templates/`, the render fixups in `scripts/lib/`, and
the wiring `values.yaml` adds (a fixup that rewrites a URL, a realm sync, an
init SQL, a resource the deploy must create). They do not test how PodiumD's
applications behave; podiumd-tests does that, on minikube and on the real
environments.

**Why:** the user's rule (2026-10-08): "tests in this project should only
cover everything for the code in this project, not anything or not much for
what runs inside minikube". Copies of application checks drift from
podiumd-tests' versions and double the work.

**How to apply:**

- A new live test asserts something this project's code makes true (a fixup
  applied, a Job or Secret the deploy creates, a limit set, the edge's
  routes); one HTTP call into an app is fine when it is the only way to see
  that, never a functional scenario.
- An application check (login flows, API behaviour, mail, forms, health of
  an app) goes to podiumd-tests: hand it over through the handoff file.
- After a deploy, `podiumd-tests run --env minikube --tier smoke`
  (read-only, no lock) checks that the applications answer.

Related: [[reuse-existing-logic]], [[remove-unused]].
