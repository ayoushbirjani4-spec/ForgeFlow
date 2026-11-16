# Review findings → where each is fixed

| # | Issue | Fix | Where |
|---|---|---|---|
| 1 | Teammates don't see the approval policy | Operating-rules block in every agent; lead always pastes a filled spawn prompt | `agents/*.md`, `templates/spawn-prompt.md`, SKILL § 6 |
| 2 | Approval was only enforced by prompts | Typed `approve APR-NNN` → `UserPromptSubmit` hook writes an HMAC-signed approval; `PreToolUse` guard enforces file globs and gated commands; optional permission rules | `scripts/ff_hook.py`, `hooks/hooks.json`, `templates/settings.forgeflow.json` |
| 3 | Teammates inherit the permission mode | Preflight check in SKILL; guard blocks writes/commands under bypass/auto/dontAsk | SKILL § 0, `ff_hook.py` `UNSAFE_MODES` |
| 4 | No `tools`/`model` limits | Per-agent `tools` and `model`; validator has no Edit/Write | `agents/*.md` |
| 5 | Not distributable | Packaged as a Claude Code plugin | `.claude-plugin/plugin.json` |
| 6 | Three sources of truth for tasks | Contract files are authoritative; native task list is dispatch only; `project.json` holds IDs | SKILL § 5, `project-state.schema.json` |
| 7 | Contradiction over who writes state | Single writer (lead); agents write only their own reports/evidence | SKILL § 5, agent rule 7 |
| 8 | Parallel writers share a directory | One Production agent by default; `ff.py validate` rejects overlapping `owned_files` | `ff.py collect_errors` |
| 9 | Scope creep relied on the agent noticing | Deterministic `scope-check` (git diff vs baseline vs approved globs), enforced in `gate` | `ff_lib.scope_report`, `ff.py gate` |
| 10 | No baseline/rollback | `init` records HEAD, branch and dirty files with hashes; plan template has a rollback section and recommends a branch | `ff_lib.record_baseline`, `implementation-plan.md` |
| 11 | Unbounded revision loop | `max_revisions` = 2, then `escalate` | `ff.py gate`, shared.md |
| 12 | Validation not independent | `validation-input` bundle excludes the producer report; validator re-runs tests itself | `ff.py validation-input`, `validation-agent.md` |
| 13 | No resume story | Resume procedure; `ff.py status` flags stale `in_progress` tasks and gives the next step | shared.md § Resume, `ff.py status` |
| 14 | Teammates can't ask the user | `approval_request` protocol to the lead; `awaiting_approval` status | shared.md § Team protocol, `task-report.schema.json` |
| 15 | Schemas missing or incomplete; combined workflow unspecified | All six schemas, `additionalProperties:false`, new statuses, signed approval records; `combined.md` with phases | `schemas/`, `workflows/combined.md` |
| 16 | `jsonschema` isn't in the standard library | Built-in subset validator; Windows `python`/`py` notes | `ff_lib.validate`, README |
| 17 | `shared.md` loading bug | Always loaded | SKILL § 0 |
| 18 | Quality score was false precision | Score removed; gate = no hard blockers and all required criteria passed | shared.md, `ff.py gate` |
| 19 | Description would over-trigger | `disable-model-invocation: true`, narrow description | SKILL frontmatter |
| 20 | Teams overused | solo / subagents / team tiers with criteria | SKILL § 2 |
| 21 | No prompt-injection defence | Untrusted-content rule everywhere; `injection_flags` in reports; `prompt_injection_followed` blocker | shared.md, agents, schemas |
| 22 | No secrets handling; `.forgeflow/` committed | Secret rule; guard blocks secret patterns in `.forgeflow/`; `.forgeflow/.gitignore` = `*` | `ff_hook.py SECRETS`, `ff.py init` |
| 23 | External data sharing | Generic-query rule; external POSTs gated | shared.md rule 5, `GATED` |
| 24 | Creator evidence not durable | `evidence.schema.json` with `retrieved_at` and an excerpt of 300 characters or fewer; validator checks saved evidence | `creator.md`, schema |
| 25 | No eval harness | 23 automated tests + 20 behavioural scenarios | `tests/`, `evals/evals.json` |
| 26 | No Windows notes | README section; `doctor` prints them | README, `ff.py doctor` |

## Minor fixes

| Nit | Fix |
|---|---|
| `+05:00` timestamp | Timestamps are generated with the real local offset (`+05:30`); schema requires an offset |
| `forgeflow-001` collisions | `project_id` = `<slug>-YYYYMMDD-<4 random>` |
| Intake/Router drawn as agents | Intake and routing are lead-run; `intake-analyst` agent removed |
| "Changes cannot be explained" not testable | Replaced by the `scope_violation` blocker from `scope-check` |
| "100% progress" checklist | Dropped; progress lives in `ff.py status` |
