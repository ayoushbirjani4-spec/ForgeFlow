"""Runs a full ForgeFlow developer flow against a throwaway git repo, driving the
real hooks and CLI exactly as Claude Code would, and prints what each gate decides.

    python examples/walkthrough.py

Nothing outside a temporary directory is touched (the approval key goes there too).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "scripts"))
import ff_lib  # noqa: E402,F401  (UTF-8 console setup)
FF = [sys.executable, str(PLUGIN / "scripts" / "ff.py")]
HOOK = [sys.executable, str(PLUGIN / "scripts" / "ff_hook.py")]
tmp = Path(tempfile.mkdtemp(prefix="ff-walkthrough-"))
repo = tmp / "pager"
env = {**os.environ, "FORGEFLOW_KEY_DIR": str(tmp / "key")}


def sh(*cmd, inp=None):
    return subprocess.run(cmd, cwd=repo, env=env, input=inp, capture_output=True, text=True)


def step(title):
    print(f"\n=== {title}")


def hook(mode, **payload):
    r = sh(*HOOK, mode, inp=json.dumps({"cwd": str(repo), "permission_mode": "default", **payload}))
    return r


def tool(name, agent=None, **ti):
    p = {"tool_name": name, "tool_input": ti}
    if agent:
        p["agent_type"] = f"forgeflow:{agent}"
    r = hook("pre", **p)
    verdict = "ALLOWED" if r.returncode == 0 else "BLOCKED"
    who = agent or "lead"
    what = ti.get("command") or Path(ti.get("file_path", "")).relative_to(repo).as_posix()
    print(f"  [{verdict}] {who:17} {name:6} {what}" + (f"\n            {r.stderr.strip()[:150]}" if r.returncode else ""))
    return r.returncode == 0


try:
    repo.mkdir(parents=True)
    (repo / "pager.py").write_text("def paginate(items, page, size):\n    return items[page*size:page*size+size+1]\n")
    (repo / "test_pager.py").write_text("from pager import paginate\n\ndef test_page():\n    assert paginate(list(range(10)), 0, 3) == [0, 1, 2]\n")
    for c in (["init", "-q"], ["config", "user.email", "demo@example.com"], ["config", "user.name", "demo"],
              ["add", "."], ["commit", "-qm", "init"]):
        sh("git", *c)
    (repo / "TODO.txt").write_text("user's own uncommitted notes\n")

    step("1. Intake: ff.py init (records the baseline, including the user's dirty TODO.txt)")
    print(sh(*FF, "init", "--name", "Pager fix", "--workflow", "developer", "--tier", "solo",
             "--request", "Fix the off-by-one in paginate").stdout)

    step("2. Before approval, nobody can touch code")
    tool("Edit", file_path=str(repo / "pager.py"), new_string="x")
    tool("Write", agent="research-agent", file_path=str(repo / ".forgeflow/evidence/EV-001.json"), content="{}")
    tool("Write", agent="research-agent", file_path=str(repo / "pager.py"), content="x")

    step("3. Lead proposes scope; the model cannot approve it")
    print(sh(*FF, "propose", "--action", "implementation", "--summary", "Fix slice end in paginate",
             "--files", "pager.py", "--risks", "callers relying on the extra item",
             "--reversibility", "git checkout pager.py").stdout)
    tool("Write", file_path=str(repo / ".forgeflow/approvals/APR-001.json"), content='{"status":"approved"}')

    step("4. The user types: approve APR-001")
    print("  " + hook("prompt", prompt="approve APR-001").stdout.strip())

    step("5. Lead writes the task contract; production works inside scope only")
    ts = json.loads((repo / ".forgeflow/project.json").read_text())
    contract = {"task_id": "DEV-001", "project_id": ts["project_id"], "assigned_agent": "production-agent",
                "objective": "Fix off-by-one in paginate", "inputs": [], "dependencies": [],
                "acceptance_criteria": [{"id": "AC-01", "text": "test_pager passes", "required": True}],
                "owned_files": ["pager.py"], "output_path": None, "approval_id": "APR-001",
                "status": "in_progress", "revision_count": 0, "max_revisions": 2,
                "created_at": ts["created_at"], "updated_at": ts["created_at"], "history": []}
    (repo / ".forgeflow/tasks/DEV-001.contract.json").write_text(json.dumps(contract, indent=2))
    ts["task_ids"] = ["DEV-001"]
    (repo / ".forgeflow/project.json").write_text(json.dumps(ts, indent=2))
    print("  " + sh(*FF, "project-status", "executing", "--decision", "solo tier", "--reason", "one-file fix").stdout.strip())
    tool("Edit", agent="production-agent", file_path=str(repo / "pager.py"), new_string="...")
    tool("Write", agent="production-agent", file_path=str(repo / "test_pager.py"), content="")
    tool("Write", agent="production-agent", file_path=str(repo / ".forgeflow/project.json"), content="{}")
    tool("Bash", agent="production-agent", command="pip install pytest-xdist")
    tool("Bash", agent="production-agent", command="git push origin main")
    (repo / "pager.py").write_text("def paginate(items, page, size):\n    return items[page*size:page*size+size]\n")

    step("6. Deterministic checks: validate + scope-check")
    print("  validate:", sh(*FF, "validate").stdout.strip())
    print(sh(*FF, "scope-check").stdout)

    step("7. Independent validation bundle (no producer claims), then a FAILED report → revise")
    print("  bundle:", sh(*FF, "validation-input", "DEV-001").stdout.strip())
    vr = {"task_id": "DEV-001", "validator": "validation-agent", "status": "fail",
          "checks": [{"id": "C1", "criterion_id": "AC-01", "status": "not_run", "severity": "major",
                      "evidence": "pytest not available in sandbox", "command": "pytest", "exit_code": None}],
          "hard_blockers": [], "tests_not_run": [{"command": "pytest", "reason": "not installed"}],
          "recommended_corrections": [], "injection_flags": []}
    (repo / ".forgeflow/tasks/DEV-001.validation.json").write_text(json.dumps(vr))
    print(sh(*FF, "gate", "DEV-001").stdout)

    step("8. Validator re-runs the test itself and it passes → gate passes")
    t = subprocess.run([sys.executable, "-c", "import test_pager; test_pager.test_page(); print('ok')"],
                       cwd=repo, capture_output=True, text=True)
    vr.update(status="pass", tests_not_run=[])
    vr["checks"] = [{"id": "C1", "criterion_id": "AC-01", "status": "passed" if t.returncode == 0 else "failed",
                     "severity": "major", "evidence": f"test_page: {t.stdout.strip() or t.stderr.strip()[:80]}",
                     "command": "python -c test_page()", "exit_code": t.returncode}]
    (repo / ".forgeflow/tasks/DEV-001.validation.json").write_text(json.dumps(vr))
    print(sh(*FF, "gate", "DEV-001").stdout)
    sh(*FF, "project-status", "delivering")

    step("9. Resume / status view")
    print(sh(*FF, "status").stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
