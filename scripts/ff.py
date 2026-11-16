"""ForgeFlow CLI (lead-only). Standard library only.

  doctor                         environment checks
  init --name --workflow --request
  baseline [--force]             re-record git baseline (refused once anything is approved)
  propose --action --summary [--files ...] [--commands ...] [--risks ...] --reversibility
  task-status ID STATUS [--note]
  project-status STATUS [--decision --reason]
  validate                       schema + cross-file checks (exit 1 on errors)
  scope-check                    changed files vs approved scope (exit 1 on violations)
  validation-input ID            independent bundle for the validator (no producer claims)
  gate ID                        pass / revise / escalate from the saved validation report
  status                         resume summary and suggested next step
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ff_lib as L  # noqa: E402

AGENT_TEAMS_ENV = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def need_root() -> Path:
    root = L.find_root(os.getcwd())
    if not root:
        die("no .forgeflow/project.json here or in any parent. Run `ff.py init` first.")
    return root


def load_project(root):
    return L.load_json(L.ff(root) / "project.json")


def save_project(root, project):
    project["updated_at"] = L.now()
    errs = L.validate(project, L.schema("project-state"))
    if errs:
        die("refusing to write invalid project.json:\n  " + "\n  ".join(errs))
    L.write_json(L.ff(root) / "project.json", project)


# ---------------------------------------------------------------- commands
def cmd_doctor(a):
    ok = True
    v = sys.version_info
    print(f"python   {v.major}.{v.minor}.{v.micro}  {'ok' if v >= (3, 9) else 'TOO OLD (need 3.9+)'}")
    ok &= v >= (3, 9)
    print(f"git      {'ok' if shutil.which('git') else 'missing (scope-check and baseline disabled)'}")
    teams = os.environ.get(AGENT_TEAMS_ENV) == "1"
    print(f"teams    {'enabled' if teams else f'disabled; set {AGENT_TEAMS_ENV}=1 for the team tier (solo/subagents still work)'}")
    print(f"key      {L.key_path()} {'exists' if L.key_path().is_file() else '(created on first approval)'}")
    print(f"schemas  {'ok' if (L.SCHEMA_DIR / 'project-state.schema.json').is_file() else 'MISSING'}")
    if os.name == "nt":
        print("windows  Agent Teams split panes need tmux/iTerm2; on Windows teammates run in-process.")
    root = L.find_root(os.getcwd())
    print(f"project  {root or 'none (run init)'}")
    sys.exit(0 if ok else 1)


def cmd_init(a):
    root = Path(os.getcwd())
    existing = L.find_root(root)
    if existing:
        die(f"ForgeFlow state already exists at {existing / L.STATE}. Inspect it with `ff.py status`; "
            "never overwrite it.")
    base = L.ff(root)
    for d in L.SUBDIRS:
        (base / d).mkdir(parents=True, exist_ok=True)
    (base / ".gitignore").write_text("*\n", encoding="utf-8")
    ts = L.now()
    project = {
        "schema_version": 2, "project_id": L.new_project_id(a.name), "project_name": a.name,
        "workflow": a.workflow, "status": "intake", "created_at": ts, "updated_at": ts,
        "user_request": a.request, "objective": a.objective or "", "requirements": [], "constraints": [],
        "acceptance_criteria": [], "task_ids": [], "approval_ids": [],
        "baseline": L.record_baseline(root), "decisions": [], "artifacts": [],
        "team": {"tier": a.tier, "members": []},
        "revision_policy": {"max_revisions": L.DEFAULT_MAX_REVISIONS}, "final_report": None,
    }
    if a.workflow == "combined":
        project["phases"] = [{"workflow": "developer", "status": "active"},
                             {"workflow": "creator", "status": "pending"}]
    save_project(root, project)
    b = project["baseline"]
    print(json.dumps({"project_id": project["project_id"], "state_dir": str(base),
                      "git": b["git"], "baseline_head": b["head"],
                      "preexisting_dirty_files": len(b["dirty_files"])}, indent=2))


def cmd_baseline(a):
    root = need_root()
    p = load_project(root)
    if L.active_approvals(root) and not a.force:
        die("approvals already exist; re-baselining would hide ForgeFlow's changes. Use --force only "
            "if the user asked for it.")
    p["baseline"] = L.record_baseline(root)
    save_project(root, p)
    print(json.dumps(p["baseline"], indent=2))


def next_apr(root) -> str:
    nums = [int(m.group(1)) for d in ("proposals", "approvals")
            for f in (L.ff(root) / d).glob("APR-*.json") if (m := re.match(r"APR-(\d+)", f.stem))]
    return f"APR-{(max(nums) + 1 if nums else 1):03d}"


def cmd_propose(a):
    root = need_root()
    p = load_project(root)
    apr = next_apr(root)
    rec = {"approval_id": apr, "project_id": p["project_id"], "action": a.action, "summary": a.summary,
           "scope": {"files": a.files, "commands": a.commands}, "risks": a.risks,
           "reversibility": a.reversibility, "status": "proposed", "proposed_at": L.now(),
           "granted_by": None, "decided_at": None, "user_statement": None, "proposal_sha256": None,
           "supersedes": a.supersedes, "signature": None}
    errs = L.validate(rec, L.schema("approval"))
    if errs:
        die("invalid proposal:\n  " + "\n  ".join(errs))
    L.write_json(L.ff(root) / "proposals" / f"{apr}.json", rec)
    p["approval_ids"].append(apr)
    p["status"] = "awaiting_approval"
    save_project(root, p)
    print(f"Proposal {apr} written. Show it to the user, then ask them to type exactly:\n"
          f"  approve {apr}    (or: reject {apr})")


def cmd_task_status(a):
    root = need_root()
    path = L.ff(root) / "tasks" / f"{a.id}.contract.json"
    if not path.is_file():
        die(f"no contract {path}")
    c = L.load_json(path)
    c["status"] = a.status
    c["updated_at"] = L.now()
    c["history"].append({"at": c["updated_at"], "status": a.status, "note": a.note or ""})
    errs = L.validate(c, L.schema("task-contract"))
    if errs:
        die("\n  ".join(errs))
    L.write_json(path, c)
    print(f"{a.id} -> {a.status}")


def cmd_project_status(a):
    root = need_root()
    p = load_project(root)
    p["status"] = a.status
    if a.decision:
        n = len(p["decisions"]) + 1
        p["decisions"].append({"id": f"DEC-{n:03d}", "decision": a.decision,
                               "reason": a.reason or "not given", "at": L.now()})
    save_project(root, p)
    print(f"project -> {a.status}")


def _globs_overlap(a: str, b: str) -> bool:
    return L.norm(a) == L.norm(b) or L.matches_any(a, [b]) or L.matches_any(b, [a])


def collect_errors(root) -> list[str]:
    errs: list[str] = []
    base = L.ff(root)
    try:
        project = load_project(root)
    except (OSError, ValueError) as e:
        return [f"project.json unreadable: {e}"]
    errs += [f"project.json {e}" for e in L.validate(project, L.schema("project-state"))]

    contracts = {}
    for f in sorted((base / "tasks").glob("*.contract.json")):
        c = L.load_json(f)
        contracts[c.get("task_id")] = c
        errs += [f"{f.name} {e}" for e in L.validate(c, L.schema("task-contract"))]
        if f.name != f"{c.get('task_id')}.contract.json":
            errs.append(f"{f.name}: task_id does not match filename")
    for tid in project.get("task_ids", []):
        if tid not in contracts:
            errs.append(f"project.json lists {tid} but tasks/{tid}.contract.json is missing")
    for tid, c in contracts.items():
        for dep in c.get("dependencies", []):
            if dep not in contracts:
                errs.append(f"{tid}: unknown dependency {dep}")
    # cycle detection
    state: dict[str, int] = {}

    def visit(t, chain):
        if state.get(t) == 1:
            errs.append("dependency cycle: " + " -> ".join(chain + [t]))
            return
        if state.get(t) == 2 or t not in contracts:
            return
        state[t] = 1
        for d in contracts[t].get("dependencies", []):
            visit(d, chain + [t])
        state[t] = 2
    for t in contracts:
        visit(t, [])
    # single writer per file among open tasks
    open_tasks = [c for c in contracts.values() if c.get("status") not in ("completed", "failed")]
    for i, x in enumerate(open_tasks):
        for y in open_tasks[i + 1:]:
            for gx in x.get("owned_files", []):
                for gy in y.get("owned_files", []):
                    if _globs_overlap(gx, gy):
                        errs.append(f"{x['task_id']} and {y['task_id']} both own '{gx}'/'{gy}'")

    for f in sorted((base / "tasks").glob("*.report.json")):
        errs += [f"{f.name} {e}" for e in L.validate(L.load_json(f), L.schema("task-report"))]
    for f in sorted((base / "tasks").glob("*.validation.json")):
        errs += [f"{f.name} {e}" for e in L.validate(L.load_json(f), L.schema("validation-report"))]
    for f in sorted((base / "evidence").glob("*.json")):
        errs += [f"evidence/{f.name} {e}" for e in L.validate(L.load_json(f), L.schema("evidence"))]
    for d in ("proposals", "approvals"):
        for f in sorted((base / d).glob("*.json")):
            rec = L.load_json(f)
            errs += [f"{d}/{f.name} {e}" for e in L.validate(rec, L.schema("approval"))]
            if d == "approvals" and rec.get("status") == "approved" and not L.signature_ok(rec):
                errs.append(f"approvals/{f.name}: signature invalid. Not written by the user's approval hook")
    for apr in project.get("approval_ids", []):
        if not (base / "proposals" / f"{apr}.json").is_file():
            errs.append(f"project.json lists {apr} but proposals/{apr}.json is missing")
    return errs


def cmd_validate(a):
    errs = collect_errors(need_root())
    if errs:
        print("INVALID\n  " + "\n  ".join(errs))
        sys.exit(1)
    print("ok")


def cmd_scope_check(a):
    rep = L.scope_report(need_root())
    print(json.dumps(rep, indent=2))
    sys.exit(1 if rep["violations"] else 0)


def cmd_validation_input(a):
    root = need_root()
    c = L.load_json(L.ff(root) / "tasks" / f"{a.id}.contract.json")
    p = load_project(root)
    head = p["baseline"]["head"]
    files = [f for f in L.changed_files(root, head) if L.matches_any(f, c["owned_files"])] if p["baseline"]["git"] else []
    diff = ""
    if files and head:
        _, diff = L.git(root, "diff", "--relative", head, "--", *files)
    lines = [f"# Validation bundle — {a.id}", "",
             "You are the independent validator. The producer's report is deliberately NOT included.", "",
             f"## Objective\n{c['objective']}", "", "## Acceptance criteria"]
    lines += [f"- **{x['id']}** ({'required' if x['required'] else 'optional'}): {x['text']}"
              for x in c["acceptance_criteria"]]
    lines += ["", "## Files in scope that changed"] + ([f"- `{f}`" for f in files] or ["- (none detected)"])
    lines += ["", "## Evidence files"] + [f"- `.forgeflow/evidence/{e.name}`"
                                          for e in sorted((L.ff(root) / 'evidence').glob('*.json'))]
    lines += ["", "## Diff vs baseline", "```diff", diff.strip() or "(no tracked diff; read new files directly)", "```",
              "", "Return one JSON object matching validation-report.schema.json."]
    out = L.ff(root) / "validation" / a.id / "input.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


def cmd_gate(a):
    root = need_root()
    cpath = L.ff(root) / "tasks" / f"{a.id}.contract.json"
    vpath = L.ff(root) / "tasks" / f"{a.id}.validation.json"
    c = L.load_json(cpath)
    if not vpath.is_file():
        die(f"no validation report at {vpath}; save the validator's JSON there first")
    v = L.load_json(vpath)
    errs = L.validate(v, L.schema("validation-report"))
    if errs:
        die("validation report invalid:\n  " + "\n  ".join(errs))
    reasons = [f"{b['code']}: {b['detail']}" for b in v["hard_blockers"]]
    passed = {ch["criterion_id"] for ch in v["checks"] if ch["status"] == "passed" and ch["criterion_id"]}
    for crit in c["acceptance_criteria"]:
        if crit["required"] and crit["id"] not in passed:
            reasons.append(f"missing_deliverable: required criterion {crit['id']} not verified as passed")
    scope = L.scope_report(root)
    owned_viol = scope["violations"]
    if owned_viol:
        reasons.append(f"scope_violation: {owned_viol}")
    if v["status"] != "pass":
        reasons.append("validator status is fail")

    if not reasons:
        decision, new_status = "pass", "completed"
    elif c["revision_count"] < c["max_revisions"]:
        decision, new_status = "revise", "needs_revision"
        c["revision_count"] += 1
    else:
        decision, new_status = "escalate", "escalated"
    c["status"] = new_status
    c["updated_at"] = L.now()
    c["history"].append({"at": c["updated_at"], "status": new_status, "note": "; ".join(reasons)[:1000] or "gate passed"})
    L.write_json(cpath, c)
    print(json.dumps({"task_id": a.id, "decision": decision, "revision_count": c["revision_count"],
                      "max_revisions": c["max_revisions"], "reasons": reasons}, indent=2))
    sys.exit(0 if decision == "pass" else 1)


def cmd_status(a):
    root = need_root()
    p = load_project(root)
    base = L.ff(root)
    contracts = [L.load_json(f) for f in sorted((base / "tasks").glob("*.contract.json"))]
    approved = {r["approval_id"] for r in L.active_approvals(root)}
    decided = {f.stem for f in (base / "approvals").glob("APR-*.json")}
    pending = [i for i in p["approval_ids"] if i not in decided]
    print(f"{p['project_name']} ({p['project_id']})  workflow={p['workflow']}  status={p['status']}  tier={p['team']['tier']}")
    for c in contracts:
        print(f"  {c['task_id']:8} {c['status']:18} {c['assigned_agent']:20} rev {c['revision_count']}/{c['max_revisions']}  {c['objective'][:60]}")
    print(f"  approvals active: {sorted(approved) or 'none'}   awaiting user: {pending or 'none'}")
    stale = [c["task_id"] for c in contracts if c["status"] == "in_progress"]
    if stale:
        print(f"  RESUME: {stale} were in_progress. Teammates are not restored; inspect partial reports/diffs, "
              "set them back to pending, and re-spawn with the spawn prompt.")
    done = {c["task_id"] for c in contracts if c["status"] == "completed"}
    ready = [c["task_id"] for c in contracts if c["status"] in ("pending", "needs_revision")
             and all(d in done for d in c["dependencies"])]
    if pending:
        nxt = f"wait for the user to type `approve {pending[0]}`"
    elif [c for c in contracts if c["status"] == "escalated"]:
        nxt = "escalated tasks need a decision from the user"
    elif ready:
        nxt = f"dispatch {ready}"
    elif contracts and len(done) == len(contracts):
        nxt = "all tasks passed; package the release and write the final report"
    else:
        nxt = "continue the plan (research/strategy) or create task contracts"
    print(f"  next: {nxt}")


def main():
    ap = argparse.ArgumentParser(prog="ff.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("init")
    s.add_argument("--name", required=True)
    s.add_argument("--workflow", required=True, choices=["developer", "creator", "combined"])
    s.add_argument("--request", required=True)
    s.add_argument("--objective")
    s.add_argument("--tier", default="team", choices=["solo", "subagents", "team"])
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("baseline")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_baseline)
    s = sub.add_parser("propose")
    s.add_argument("--action", required=True, choices=L.schema("approval")["properties"]["action"]["enum"])
    s.add_argument("--summary", required=True)
    s.add_argument("--files", nargs="*", default=[])
    s.add_argument("--commands", nargs="*", default=[])
    s.add_argument("--risks", nargs="*", default=[])
    s.add_argument("--reversibility", required=True)
    s.add_argument("--supersedes")
    s.set_defaults(fn=cmd_propose)
    s = sub.add_parser("task-status")
    s.add_argument("id")
    s.add_argument("status")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_task_status)
    s = sub.add_parser("project-status")
    s.add_argument("status", choices=L.schema("project-state")["properties"]["status"]["enum"])
    s.add_argument("--decision", help="record a decision alongside the status change")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_project_status)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("scope-check").set_defaults(fn=cmd_scope_check)
    s = sub.add_parser("validation-input")
    s.add_argument("id")
    s.set_defaults(fn=cmd_validation_input)
    s = sub.add_parser("gate")
    s.add_argument("id")
    s.set_defaults(fn=cmd_gate)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
