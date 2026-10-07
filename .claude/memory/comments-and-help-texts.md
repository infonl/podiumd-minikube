---
name: comments-and-help-texts
description: "Comments, docstrings, help texts and messages in podiumd-minikube are minimal and precise"
metadata:
  type: feedback
---

# Comments and help texts

Adapted (2026-10-07) from the user's rule for podiumd-tests. Applies to
comments in `values.yaml`, `templates/`, `scripts/` and `tests/`, script
usage texts, `echo`ed messages, test skip reasons and error messages.

**Why:** the user wants text that is cheap to read and stays true; long or
vague text rots and hides the one fact that matters.

**How to apply:**

- Say only what the code cannot: the why, a constraint, a non-obvious fact
  ("ITA hardcodes RequireHttpsMetadata=true"). Never restate what the code
  does.
- One sentence where one sentence does; no history, no "now/new/changed", no
  references to a session. History and the reasoning behind a fix belong in
  `.claude/plans/plan.md`, not in the code.
- Docstrings: one line stating what it returns or does. Add a second
  paragraph only for a real constraint or edge case.
- Usage and help texts: what the option does and its default, in under one
  line; no examples unless the syntax is unclear.
- Errors, warnings and skip reasons: the fact, the object and the fix in one
  line ("kubectl context is 'aks-x', not 'minikube': run `kubectl config
  use-context minikube`").
- Exact terms: name the real object, field, command or limit; no "some",
  "various", "etc.", hedges or filler.
- Same term for the same thing everywhere (one word, one meaning).
- Existing long comments are trimmed when the code around them changes, not
  in a separate sweep.
- Before committing, reread each new comment and message and cut every word
  that does not change the meaning.

Related: [[reuse-existing-logic]].
