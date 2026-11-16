# Implementation plan — {{project_name}} ({{project_id}})

## Problem statement

## Approach
### Alternatives considered and trade-offs

## Scope (becomes the approval proposal)
| Glob | Create / modify | Why |
|---|---|---|

Commands requiring approval (exact prefixes):
- e.g. `git switch -c forgeflow/<slug>` (recommended: work on a new branch)

## Tasks
| ID | Agent | Objective | Depends on | Owned files |
|---|---|---|---|---|

## Acceptance criteria
| ID | Criterion | Required | Verified by (command / check) |
|---|---|---|---|

## Test strategy
Concrete commands: tests, type check, lint.

## Risks

## Rollback
Baseline HEAD `{{baseline_head}}`. How to undo: revert the listed files /
delete the branch. The user's pre-existing changes ({{dirty_count}} files) are untouched.
