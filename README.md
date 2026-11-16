# ForgeFlow (plugin, v0.2.0)

An approval-gated multi-agent workflow for Claude Code. It takes a software
task or a content brief through research, plan, **your approval**,
production, independent validation, and release packaging.

The approval gate is enforced by hooks, not by prompts: before you type
`approve APR-NNN`, agents physically cannot edit project files or run gated
commands (push, install, deploy, migrate, recursive delete, external POSTs).

## Layout

```
forgeflow/
├── .claude-plugin/plugin.json
├── skills/forgeflow/
│   ├── SKILL.md                 lead / orchestrator instructions (/forgeflow:forgeflow only)
│   ├── workflows/               shared.md (always) + developer | creator | combined
│   ├── schemas/                 project-state, task-contract, task-report,
│   │                            validation-report, approval, evidence
│   └── templates/               plan, brief, final report, spawn prompt, settings
├── agents/                      research, strategy, production, validation,
│                                optimization, release (with tool limits + models)
├── hooks/hooks.json             PreToolUse guard + UserPromptSubmit approval recorder
├── scripts/ff.py                lead CLI (init, propose, validate, scope-check, gate, status…)
├── scripts/ff_hook.py           the enforcement hooks
├── scripts/ff_lib.py            stdlib-only helpers incl. a small JSON-schema validator
├── tests/test_forgeflow.py      23 end-to-end tests
└── evals/evals.json             behavioural scenarios for the whole workflow
```

## Install

1. This folder is both the plugin and a one-plugin local marketplace
   (`.claude-plugin/marketplace.json`). From a terminal:
   ```bash
   claude plugin validate "<path-to-this-folder>"
   claude plugin marketplace add "<path-to-this-folder>"
   claude plugin install forgeflow@forgeflow-local
   ```
   (or `/plugin marketplace add <path>` inside a session). Restart Claude Code.
   The skill is then `/forgeflow:forgeflow`.
2. For the team tier, add to `~/.claude/settings.json`:
   `{"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}}`, then restart.
3. Optional defence in depth: merge `skills/forgeflow/templates/settings.forgeflow.json`
   into the **target project's** `.claude/settings.json`.
4. In the target project: `python <plugin>/scripts/ff.py doctor`.

Requirements: Python 3.9+ on PATH as `python` (edit `hooks/hooks.json` to use
`py` if needed) and git. No pip packages.

## Use

```
/forgeflow:forgeflow developer Fix the off-by-one in pagination
```
The lead inspects, researches and plans, then shows a proposal. You reply with
`approve APR-001` (or `reject APR-001`, and later `revoke APR-001`). That has
to be a typed chat message, because a hook that only fires on real user input
records it. Choices picked in a question dialog do not count.

Run ForgeFlow in **default** or **plan** permission mode. Teammates inherit
the lead's mode, and the guard refuses writes and commands under
bypass/auto/dontAsk.

## How the enforcement works

| Mechanism | Where |
|---|---|
| Approvals can only be created by your typed message (`UserPromptSubmit` hook) and are HMAC-signed with a key in `~/.forgeflow/approval.key` | `ff_hook.py prompt` |
| No tool may read or write `.forgeflow/approvals/` or the key | `ff_hook.py pre` |
| Edits outside `.forgeflow/` must match an approved file glob | `ff_hook.py pre` |
| Gated shell commands must start with an approved command prefix; each `&&` / `;` / `\|` segment is checked separately | `ff_hook.py pre` |
| Editing a proposal after approval invalidates the approval (hash check) | `ff_lib.active_approvals` |
| Secret patterns can't be written into `.forgeflow/` | `ff_hook.py pre` |
| Writes made via the shell are caught after the fact by `ff.py scope-check` (git diff vs baseline) | `ff.py scope-check`, `ff.py gate` |
| Per-role write limits from the hook's `agent_type` field: research → evidence only, strategy → plans only, validation → nothing, release → artifacts/reports; no specialist can write `project.json`, contracts, proposals or validation reports | `ff_hook.py ROLE_WRITES` |
| Hook errors inside an active project block the call (fail closed) | `ff_hook.py main` |

**Limits, stated honestly.** This is tamper-*resistant*, not tamper-proof. A
determined agent with shell access could, for example, write files using an
interpreter one-liner. The guard can't see that at call time; `scope-check`
and the gate catch it afterwards. If `python` itself is missing, hooks cannot
run at all. `ff.py doctor` checks for this. Claude Code's own permission
prompts remain the outer layer.

## Windows notes

- Agent Teams split-pane display needs tmux or iTerm2. On Windows, teammates
  run in-process in the same terminal.
- Hooks are invoked as `python "…/ff_hook.py"`. If only the `py` launcher
  exists, change both commands in `hooks/hooks.json`.
- Paths are compared case-insensitively on Windows.

## What the Claude Code docs confirm

- Teammates **can** be spawned from plugin agent definitions ("spawn a
  teammate using the forgeflow:production-agent agent type"). `tools` and
  `model` apply; the definition's `skills` do not. In split-pane mode the agent
  body replaces the default prompt, which is why the lead also pastes the spawn
  prompt.
- Hook input carries `agent_id` / `agent_type` for subagents and in-process
  teammates, which is what the per-role limits use.
- Teammates start in the lead's permission mode, including bypass. Their
  permission prompts appear in the lead's session.
- `/resume` does not restore in-process teammates; there is one team per
  session and no nested teams. Hence `ff.py status` and the resume procedure.
- Plugin agents ignore `hooks`, `mcpServers` and `permissionMode` frontmatter.
  ForgeFlow uses none of them.

## Still to check in a live session

1. Docs say hooks run inside subagents but don't say so explicitly for
   **split-pane** teammates. Before approving anything, ask a teammate to edit
   a file; the guard should block it.
2. That `agent_type` arrives as `forgeflow:<name>` or `<name>` for plugin
   agents. The guard accepts either.

## License

None is set yet. Add a `LICENSE` file or a `license` field in `plugin.json`
before publishing; `claude plugin validate` warns about this.

## Tests

```bash
python -m unittest discover -s tests
```

To watch every gate fire on a throwaway repo (nothing outside a temp dir is touched):

```bash
python examples/walkthrough.py
```
