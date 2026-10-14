# Remove what is unused

Adapted (2026-10-07) from the user's rule for podiumd-tests
(`infonl/podiumd-tests/.claude/memory/remove-unused.md`). Anything that
nothing uses is removed, not kept "just in case": code in `src/`,
`unit_tests/` and `tests/`, templates, `values.yaml` overrides, vendored files, fixtures,
helpers, constants, comments and doc sections.

**Why:** the user's words: "dead code, or dead test seeding should be
removed". Unused parts still cost deploy time, review and reading, and they
go stale (the PKCE-only ita/kiss placeholders here did).

**How to apply:**

- When a check, deploy or investigation shows something is never used,
  remove it in the same change and say so in the commit message.
- vulture covers Python code. For templates, values overrides, vendored
  files (`vendor/dimpact-zaakafhandelcomponent/NOTES.md` lists each one's
  consumer) and docs, check by hand which template, script or test reads
  them.
- A `values.yaml` setting that matches what ExternalsPodiumD or
  podiumd-infra deploy is environment config, not unused: keep it even when
  no test here reads it ([[reference-environments]]). Only drop such a
  setting when the reference projects drop it, or when it has no effect in
  the rendered manifest.
- Copying a reference file is no reason to keep a part of it that has no
  effect here.
- Code needed only for an older podiumd release gets a `podiumd<X.Y:`
  comment (X.Y the first release without the need). When a release stops
  being deployed, raise `OLDEST_PODIUMD` in
  `unit_tests/test_release_leftovers.py` and remove what it lists. Never
  pin a podiumd image tag in `values.yaml` (the same test fails on one);
  our own components take podiumd's images (`chart.bundled_image_sets`).

Related: [[reuse-existing-logic]], [[comments-and-help-texts]].
