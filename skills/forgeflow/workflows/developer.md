# Developer workflow

Task IDs: `DEV-001`, `DEV-002`, …

## 1. Inspection (read-only)
- Structure, language, framework, package config, test/lint/type-check commands.
- `git status`: the baseline recorded by `FF init` lists the user's existing
  uncommitted files. Never overwrite or "clean up" them.
- Never read secrets into state (shared rule 4).

## 2. Research
- Verify APIs and framework behaviour against official docs for the
  installed version. Record each source as an evidence file.
- Note security, compatibility and migration risks.

## 3. Plan (`templates/implementation-plan.md`)
Must include: the exact file globs to change or create, the commands needing
approval (new branch, installs, migrations), acceptance criteria marked
required/optional, the test strategy with concrete commands, and a rollback
plan. **Recommend a new branch** (`git switch -c forgeflow/<slug>`) and list it
in `--commands` if the user wants one.

## 4. Approval
`FF propose --action implementation ...` with the plan's globs and commands.

## 5. Implementation
- Follow existing conventions; make minimal changes; add no unapproved dependencies.
- Never delete or weaken tests to make them pass.
- A need outside scope (a migration, new file, new dependency) means stop and
  send an `approval_request`.

## 6. Validation
- `FF scope-check` first.
- `FF validation-input DEV-00N` gives the bundle: criteria plus diff, with no
  production claims.
- The validator re-runs tests, type checks and lint itself and records the
  real exit codes. Tests not run are listed with a reason.

## 7. Delivery
Changed files (ForgeFlow's vs the user's pre-existing ones), test output,
limitations, docs, changelog, and a draft commit message or PR description.
Commit, push, PR and deploy each need their own approval.
