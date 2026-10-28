---
name: reference-environments
description: "podiumd-minikube delivers an environment as identical as possible to podiumd-infra and ExternalsPodiumD; ExternalsPodiumD wins in case of doubt"
metadata:
  type: feedback
---

# Reference environments

podiumd-minikube delivers an environment as identical as possible to the one
podiumd-infra (`icatt-menselijk-digitaal/podiumd-infra`) and ExternalsPodiumD
(`scctwente/ExternalsPodiumD`) deploy. In case of doubt, ExternalsPodiumD takes
precedence.

**Why:** the user's rule (2026-10-07). podiumd-tests runs against minikube
first and against the real environments later; a minikube setting that differs
makes the tests prove something the real environments do not do.

**How to apply:**

- Before changing an app setting, a client id, a token, a Job or a wiring
  between components, look up how both projects configure it in their values
  files, and match it. Name what each does in the change and in `plan.md`.
- ExternalsPodiumD holds the test, acceptance and production environments;
  podiumd-infra tests earlier, so it can carry features that ExternalsPodiumD
  does not have yet. Include such optional functionality anyway (the
  podiumd-infra way), so broader tests can run. Where both configure the same
  thing differently, ExternalsPodiumD's way wins.
- Deviate where minikube cannot do the same (single node, no Azure, no
  cloud operators, no public DNS), or where it serves minikube's own purposes
  (the user, 2026-10-28): a low footprint (memory first, then CPU), a very
  fast bootstrap and (re)deploy, debug facilities, and suitability for
  developers. Never where it makes the tests prove something the estates do
  not do. Record each deviation with its reason in `values.yaml` or the code,
  and in `plan.md`.
- How it is deployed follows ExternalsPodiumD's pipelines: Helm releases
  `podiumd` (namespace `podiumd`) and `monitoring-logging` (namespace
  `monitoring`), their Helm flags and its one-shot Job list. "Exactly" is too
  much (the user, 2026-10-28): the deviations above stay.
- Requests from podiumd-tests are checked against both projects the same way.
  What ExternalsPodiumD
  and podiumd-infra configure outweighs what a podiumd-tests handoff asks for
  (the user, 2026-10-07): where they differ, follow the reference projects and
  tell podiumd-tests.

Related: [[reuse-existing-logic]], [[comments-and-help-texts]].
