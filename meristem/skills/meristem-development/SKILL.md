---
name: meristem-development
description: Develop and release the meristem harness repository. Use when bumping the version, tagging or publishing a release.
---

# meristem development

Canonical rules live in `AGENTS.md`; this skill repeats the release procedure exactly.

## Spec-driven flow

Run this before any code. Full table and gates: the Spec-driven development section of `AGENTS.md` and the [spec standard](https://github.com/Knuckles-Team/pipelines/blob/main/reference/spec-standard.md).

1. Read `.specify/memory/constitution.md`; create it from `.specify/templates/constitution-template.md` if absent (`speckit-constitution`).
2. Specify the feature: `speckit-specify` gathers the inputs and writes `specs/<stable-id>/spec.md` and `requirements.md`.
3. Clarify: resolve every `[NEEDS CLARIFICATION: ...]` marker (`speckit-clarify`).
4. Plan: `plan.md` from `.specify/templates/plan-template.md`, including Build contract, Production caller chain and Cross-repo seams (`speckit-plan`).
5. Tasks: `tasks.md` lines `- [ ] T001 [P] [US1] <ROW-ID> ...` (`speckit-tasks`).
6. Analyze: fix the gaps and fill `checklists/requirements.md` (`speckit-analyze`).
7. Implement and land with the delivery contract in this skill (`speckit-implement`); release through pipelines `releases/*.yml` bundles.

Never write code for a row that is not SPECIFIED. Feature directories use the stable spec ID, never a numbered prefix. The state is computed from rows, `Spec:` trailers and test bindings; never write `status.json` or a `Status:` line.

## Delivery contract

Specs are already designed. The work is implementation.

**A row is delivered when**
- its behaviour is fully implemented, as the row says;
- it is wired to its production entry point (a production caller exists outside the new code and its tests);
- every test level its test-spec names passes;
- the tests are bound to the row;
- the gates verify it.

There is no time limit, no size limit per agent or per change, and no rule about how many agents deliver a row.

**Recommendations (optional)**
- Extend first: find the spec row that owns the behaviour (`git grep -n "<term>" -- specs`), cite its ID in the commit `Spec:` trailer, and reuse the existing function or module rather than adding a second implementation.
- Group rows that share an entry point into one bundle and deliver bundles in dependency order, producer first. Split a requirement into `<ID>.<n>` children when it names more than 2 code roots or is cross-repo; the destination repository copies a behaviour first, then the source switches importers and deletes.
- Net-new work can be sliced: `.1` typed model plus validation and refusal tests, `.2` the one entry point that uses it, `.3`+ each further behaviour.
- Every delivered ID gets at least one test bound to it in the same PR. Python: `@pytest.mark.spec("<ID>")`. Rust and TypeScript: a `// spec: <ID>` line directly above the test. Never write generated status files; the state is computed.
- Run the targeted tests for the change; leave the full suite to the main-branch gates.
- Report the PR URL, requirement IDs, test result, and any `ID:<main sha>` proof for rows already on main.

**Before every push**
- Work in a real `git worktree add` from `origin/main`. Never edit a shared checkout, never `git stash`, never `git add -A`, never force-push.
- Run the repo's own pre-commit on the changed files: `uvx --from pre-commit==4.6.0 pre-commit run --files $(git diff --name-only origin/main...HEAD)`. Fix every failure. No `noqa`, `type: ignore`, skip, xfail or whitelist entries to pass a gate.

**Landing**
- Open the pull request and merge it when every required check is green. Mechanical conflicts (generated files, Markdown) resolve by merging `origin/main`; real conflicts are additive in most cases and are resolved, not deferred.
- Full CI on main catches what the pull-request subset missed; regressions are fixed forward.

## Release

Do not push, tag or publish without explicit scope. Follow in order.

1. Bump first. Version lives in `meristem/_version.py` (pyproject reads it dynamically), `README.md` (`*Version: X.Y.Z*`) and `CHANGELOG.md`; `.bumpversion.cfg` `current_version` must match. Run `bump-my-version`/`bumpversion` with `--no-commit --no-tag --new-version X.Y.Z` (or edit all four by hand), then `grep -rn 'OLD_VERSION' meristem/_version.py README.md CHANGELOG.md .bumpversion.cfg` to confirm none still show the old value. The editable root entry in `uv.lock` carries no version, so `uv lock --check` must pass; refresh it only if it fails. This repo has no `scripts/check_version_consistency.py` and no recorded fixture embeds the version.
2. Merge the bump PR to `main` (rebase merge). Never tag a branch commit.
3. Verify the merged commit `<sha>`: `git show <sha>:meristem/_version.py` shows `__version__ = "X.Y.Z"`, `git show <sha>:.bumpversion.cfg` shows `current_version = X.Y.Z`, and `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/meristem/X.Y.Z/json` prints `404`.
4. Only then push the tag on that commit: `git tag vX.Y.Z <sha> && git push origin vX.Y.Z`. `release.yml` publishes only from an exact `vX.Y.Z` tag and fails if the tag differs from `_version.py`, but PyPI refuses a rebuilt old version, so verify before tagging.
5. Confirm PyPI has both files: `curl -s https://pypi.org/pypi/meristem/X.Y.Z/json` lists a `bdist_wheel` and an `sdist` in `urls`. `release.yml` runs `uv publish dist/*` after `uv build`, which uploads both; report it if either is missing.
6. Wrong-commit tag: if nothing was published, delete and re-tag (`git push origin :refs/tags/vX.Y.Z`, `git tag -d vX.Y.Z`, then step 4). If anything was published, never reuse the version; cut the next patch from step 1.
