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
- Deviate only where minikube cannot do the same (single node, no Azure, no
  cloud operators, no public DNS), and record each deviation with its reason
  in `values.yaml` and `plan.md`.
- Requests from podiumd-tests are checked against both projects the same way,
  and wait for the user's go before they are implemented.

Related: [[reuse-existing-logic]], [[comments-and-help-texts]].
