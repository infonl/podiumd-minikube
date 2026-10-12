# Test scope: this project's code, not what runs inside minikube

The tests here (`tests/` live, `unit_tests/` unit) cover the code of this
project: `src/podiumd_minikube/`, `templates/`, the render fixups in `manifests`, and
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
  an app) goes to podiumd-tests: send it to its session.
- After a deploy, `podiumd-tests run --env minikube --tier smoke`
  (read-only, no lock) checks that the applications answer.

## Division of work with podiumd-tests

Agreed 2026-10-12; podiumd-tests keeps its side in its
`.claude/memory/coordination-podiumd-minikube.md`. Every object has one
manager: two repositories that both create or prune it undo each other.

- podiumd-minikube: the components and their wiring, the edge and its
  routes, the Keycloak clients and client roles of helm-charts'
  `charts/podiumd/templates/keycloak-podiumd-realm-config.yaml`, the Open
  Notificaties kanalen in `values.yaml` `notifications_kanalen_config` (as
  ExternalsPodiumD and podiumd-infra), and the opt-in Frank!Gateway with
  OpenBao (profile `frankgateway`).
- podiumd-tests' bootstrap creates and removes: test users, test clients and
  tokens, realm roles, the DigiD/eHerkenning OIDC configuration, Open Inwoner
  CMS pages and contact form, Open Formulieren test forms, and any further
  kanalen. It deploys its own mock server (webhook callbacks, NotifyNL,
  Worldline) from its `infra/`. podiumd-minikube creates none of these.
- Neither: eSuite (external) and Alertmanager routes.

Related: [[reuse-existing-logic]], [[remove-unused]], [[reference-environments]].
