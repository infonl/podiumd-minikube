# Python checks before every commit

`./run_python_checks` (ruff, pylint, basedpyright, vulture, jscpd, pytest and more) guards
every commit of podiumd-minikube, including commits that touch only
`values.yaml`, JSON or docs.

**Why:** the user wants every commit to pass the checks, so each atomic
commit stays usable on its own. Commits went in broken because the checks
ran before the last edit, or a pipe hid their exit code.

**How to apply:**

- Run `./run_python_checks` right before each commit, after the last edit.
  Judge it by its exit code or its last line `=== all checks passed ===`,
  never by ruff's early "All checks passed!".
- Never pipe the checks into a chained commit or push
  (`./run_python_checks | tail -1; git commit ... && git push` hides a
  failure).
- The pre-commit hook `.githooks/pre-commit` runs them too
  (`git config core.hooksPath .githooks`); never commit with `--no-verify`.
- Chain `git add <paths> && git commit`: a failed `git add` must stop the
  commit. Check `git diff --cached` first; a file with other pending edits
  is committed after those edits, or only the fix is staged.
- Run the full checks once per commit, not after every edit: other
  sessions share the laptop's CPU. `run_python_checks` lowers its own
  priority (`nice -n 10`) and uses at most 4 workers; run other heavy local
  commands (test suites, builds) the same way.
- podiumd-tests leads the check setup shared with podiumd-infra and
  helm-charts (`run_python_checks`, `pyproject.toml` tool settings, CI
  check workflows). When in doubt about a flag, wording or structure, copy
  its version (its working tree may hold uncommitted changes); deviate only
  for a project difference, and say so in the commit.

Related: [[reuse-existing-logic]], [[comments-and-help-texts]],
[[laptop-resources]].
