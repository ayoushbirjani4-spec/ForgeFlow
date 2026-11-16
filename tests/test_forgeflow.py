"""End-to-end tests for ForgeFlow scripts and hooks.  Run:  python -m unittest discover -s tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FF = ROOT / "scripts" / "ff.py"
HOOK = ROOT / "scripts" / "ff_hook.py"
sys.path.insert(0, str(ROOT / "scripts"))
import ff_lib as L  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fftest-"))
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.env = {**os.environ, "FORGEFLOW_KEY_DIR": str(self.tmp / "key")}
        os.environ["FORGEFLOW_KEY_DIR"] = self.env["FORGEFLOW_KEY_DIR"]
        g = lambda *a: subprocess.run(["git", *a], cwd=self.proj, capture_output=True, check=True)
        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        (self.proj / "src").mkdir()
        (self.proj / "src" / "app.py").write_text("print('hi')\n")
        (self.proj / "README.md").write_text("readme\n")
        g("add", ".")
        g("commit", "-qm", "init")
        (self.proj / "notes.txt").write_text("user's uncommitted work\n")  # pre-existing dirty file

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ff(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(FF), *args], cwd=self.proj, env=self.env,
                           capture_output=True, text=True)
        if ok and r.returncode != 0:
            self.fail(f"ff {args} failed: {r.stdout}{r.stderr}")
        return r

    def hook(self, mode, payload):
        payload = {"cwd": str(self.proj), "permission_mode": "default", **payload}
        return subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(payload),
                              env=self.env, capture_output=True, text=True)

    def pre(self, tool, **ti):
        return self.hook("pre", {"tool_name": tool, "tool_input": ti})

    def init(self):
        self.ff("init", "--name", "Auth Feature", "--workflow", "developer", "--request", "add auth")

    def approve(self, apr="APR-001"):
        r = self.hook("prompt", {"prompt": f"approve {apr}"})
        self.assertIn("APPROVED", r.stdout)


class TestValidator(unittest.TestCase):
    def test_subset(self):
        s = {"type": "object", "additionalProperties": False, "required": ["a"],
             "properties": {"a": {"type": "string", "pattern": "^x"}, "b": {"anyOf": [{"type": "null"}, {"enum": [1]}]}}}
        self.assertEqual(L.validate({"a": "xy", "b": None}, s), [])
        self.assertTrue(L.validate({"a": "y"}, s))
        self.assertTrue(L.validate({"a": "x", "c": 1}, s))
        self.assertTrue(L.validate({"a": "x", "b": 2}, s))
        self.assertTrue(L.validate({}, s))

    def test_glob(self):
        self.assertTrue(L.matches_any("src/a/b.py", ["src/**"]))
        self.assertTrue(L.matches_any("src/b.py", ["src/**/*.py"]))
        self.assertFalse(L.matches_any("src/a/b.py", ["src/*.py"]))
        self.assertFalse(L.matches_any("tests/b.py", ["src/**"]))

    def test_timestamp_offset(self):
        self.assertRegex(L.now(), r"[+-]\d{2}:\d{2}$")


class TestInitAndState(Base):
    def test_init_baseline_and_gitignore(self):
        self.init()
        p = json.loads((self.proj / ".forgeflow" / "project.json").read_text())
        self.assertRegex(p["project_id"], r"^auth-feature-\d{8}-[a-z0-9]{4}$")
        self.assertTrue(p["baseline"]["git"])
        self.assertEqual([d["path"] for d in p["baseline"]["dirty_files"]], ["notes.txt"])
        self.assertEqual((self.proj / ".forgeflow" / ".gitignore").read_text(), "*\n")
        self.ff("validate")

    def test_init_refuses_overwrite(self):
        self.init()
        r = self.ff("init", "--name", "x", "--workflow", "creator", "--request", "y", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("never overwrite", r.stderr)

    def test_inactive_when_no_project(self):
        self.assertEqual(self.pre("Write", file_path=str(self.proj / "src/app.py"), content="x").returncode, 0)


class TestGuard(Base):
    def setUp(self):
        super().setUp()
        self.init()

    def test_code_write_blocked_before_approval(self):
        r = self.pre("Edit", file_path=str(self.proj / "src" / "app.py"), new_string="x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("outside every approved scope", r.stderr)

    def test_state_write_allowed_and_secret_blocked(self):
        self.assertEqual(self.pre("Write", file_path=str(self.proj / ".forgeflow/plans/p.md"), content="plan").returncode, 0)
        r = self.pre("Write", file_path=str(self.proj / ".forgeflow/evidence/EV-001.json"),
                     content="key AKIAABCDEFGHIJKLMNOP")
        self.assertEqual(r.returncode, 2)
        self.assertIn("secret", r.stderr)

    def test_approvals_dir_and_key_protected(self):
        for tool, ti in [("Write", {"file_path": str(self.proj / ".forgeflow/approvals/APR-001.json"), "content": "{}"}),
                         ("Bash", {"command": "echo {} > .forgeflow/approvals/APR-001.json"}),
                         ("Read", {"file_path": str(Path.home() / ".forgeflow/approval.key")})]:
            self.assertEqual(self.pre(tool, **ti).returncode, 2, tool)

    def test_unsafe_mode_blocked(self):
        r = self.hook("pre", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "permission_mode": "bypassPermissions"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("disables human approval", r.stderr)

    def test_outside_project_blocked(self):
        r = self.pre("Write", file_path=str(Path.home() / ".claude" / "settings.json"), content="{}")
        self.assertEqual(r.returncode, 2)

    def test_approval_flow_scope_and_commands(self):
        self.ff("propose", "--action", "implementation", "--summary", "auth",
                "--files", "src/**", "--commands", "git switch -c forgeflow/auth", "--reversibility", "revert files")
        self.assertEqual(self.pre("Edit", file_path=str(self.proj / "src/app.py"), new_string="x").returncode, 2)
        # The model cannot approve via a question dialog; only the user's typed message counts.
        self.approve()
        self.assertEqual(self.pre("Edit", file_path=str(self.proj / "src/app.py"), new_string="x").returncode, 0)
        self.assertEqual(self.pre("Write", file_path=str(self.proj / "README.md"), content="x").returncode, 2)
        self.assertEqual(self.pre("Bash", command="git switch -c forgeflow/auth").returncode, 0)  # not gated
        self.assertEqual(self.pre("Bash", command="git push origin main").returncode, 2)
        self.assertEqual(self.pre("Bash", command="npm test && npm install left-pad").returncode, 2)
        self.assertEqual(self.pre("PowerShell", command="Remove-Item -Recurse -Force src").returncode, 2)
        self.assertEqual(self.pre("Bash", command="pytest -q").returncode, 0)

    def test_gated_command_allowed_by_exact_prefix(self):
        self.ff("propose", "--action", "dependency_install", "--summary", "add requests",
                "--commands", "pip install requests==2.32.3", "--reversibility", "pip uninstall")
        self.assertEqual(self.pre("Bash", command="pip install requests==2.32.3").returncode, 2)
        self.approve()
        self.assertEqual(self.pre("Bash", command="pip install requests==2.32.3").returncode, 0)
        self.assertEqual(self.pre("Bash", command="pip install requests==2.32.3 && rm -rf /").returncode, 2)
        self.assertEqual(self.pre("Bash", command="pip install evil").returncode, 2)

    def test_tampered_proposal_or_forged_approval_rejected(self):
        self.ff("propose", "--action", "implementation", "--summary", "s", "--files", "src/**", "--reversibility", "r")
        self.approve()
        prop = self.proj / ".forgeflow/proposals/APR-001.json"
        d = json.loads(prop.read_text()); d["scope"]["files"] = ["**"]; prop.write_text(json.dumps(d))
        self.assertEqual(self.pre("Write", file_path=str(self.proj / "README.md"), content="x").returncode, 2)
        self.assertEqual(self.pre("Edit", file_path=str(self.proj / "src/app.py"), new_string="x").returncode, 2)
        # forged approval file without a valid signature
        appr = self.proj / ".forgeflow/approvals/APR-001.json"
        f = json.loads(appr.read_text()); f["scope"]["files"] = ["**"]; f["proposal_sha256"] = L.sha256_file(prop)
        appr.write_text(json.dumps(f))
        self.assertEqual(self.pre("Write", file_path=str(self.proj / "README.md"), content="x").returncode, 2)
        r = self.ff("validate", ok=False)
        self.assertIn("signature invalid", r.stdout)

    def test_reject_and_revoke(self):
        self.ff("propose", "--action", "implementation", "--summary", "s", "--files", "src/**", "--reversibility", "r")
        self.assertIn("REJECTED", self.hook("prompt", {"prompt": "reject APR-001"}).stdout)
        self.assertEqual(self.pre("Edit", file_path=str(self.proj / "src/app.py"), new_string="x").returncode, 2)
        self.ff("propose", "--action", "implementation", "--summary", "s2", "--files", "src/**", "--reversibility", "r")
        self.approve("APR-002")
        self.assertEqual(self.pre("Edit", file_path=str(self.proj / "src/app.py"), new_string="x").returncode, 0)
        self.assertIn("REVOKED", self.hook("prompt", {"prompt": "revoke APR-002"}).stdout)
        self.assertEqual(self.pre("Edit", file_path=str(self.proj / "src/app.py"), new_string="x").returncode, 2)

    def as_role(self, role, path, content="x"):
        return self.hook("pre", {"tool_name": "Write", "agent_type": role,
                                 "tool_input": {"file_path": str(self.proj / path), "content": content}})

    def test_role_write_limits(self):
        self.ff("propose", "--action", "implementation", "--summary", "s", "--files", "src/**", "--reversibility", "r")
        self.approve()
        self.assertEqual(self.as_role("forgeflow:research-agent", ".forgeflow/evidence/EV-001.json").returncode, 0)
        self.assertEqual(self.as_role("forgeflow:research-agent", "src/app.py").returncode, 2)       # even though approved
        self.assertEqual(self.as_role("research-agent", ".forgeflow/plans/p.md").returncode, 2)
        self.assertEqual(self.as_role("validation-agent", ".forgeflow/tasks/DEV-001.report.json").returncode, 2)
        self.assertEqual(self.as_role("production-agent", "src/app.py").returncode, 0)
        self.assertEqual(self.as_role("production-agent", "README.md").returncode, 2)
        self.assertEqual(self.as_role("production-agent", ".forgeflow/tasks/DEV-001.report.json").returncode, 0)
        for lead_file in (".forgeflow/project.json", ".forgeflow/tasks/DEV-001.contract.json",
                          ".forgeflow/proposals/APR-009.json"):
            self.assertEqual(self.as_role("production-agent", lead_file).returncode, 2, lead_file)
        self.assertEqual(self.as_role("some-other-agent", ".forgeflow/project.json").returncode, 2)
        # the lead (no agent_type) still owns state
        self.assertEqual(self.pre("Write", file_path=str(self.proj / ".forgeflow/project.json"), content="{}").returncode, 0)

    def test_unrelated_prompt_ignored(self):
        self.assertEqual(self.hook("prompt", {"prompt": "please approve the plan"}).stdout, "")


class TestScopeAndGate(Base):
    def setUp(self):
        super().setUp()
        self.init()
        self.ff("propose", "--action", "implementation", "--summary", "s", "--files", "src/**", "--reversibility", "r")
        self.approve()
        ts = L.now()
        pid = json.loads((self.proj / ".forgeflow/project.json").read_text())["project_id"]
        self.contract = {"task_id": "DEV-001", "project_id": pid, "assigned_agent": "production-agent",
                         "objective": "implement", "inputs": [], "dependencies": [],
                         "acceptance_criteria": [{"id": "AC-01", "text": "tests pass", "required": True}],
                         "owned_files": ["src/**"], "output_path": None, "approval_id": "APR-001",
                         "status": "in_progress", "revision_count": 0, "max_revisions": 2,
                         "created_at": ts, "updated_at": ts, "history": []}
        L.write_json(self.proj / ".forgeflow/tasks/DEV-001.contract.json", self.contract)
        p = json.loads((self.proj / ".forgeflow/project.json").read_text())
        p["task_ids"] = ["DEV-001"]
        L.write_json(self.proj / ".forgeflow/project.json", p)

    def report(self, status="pass", crit_status="passed", blockers=()):
        L.write_json(self.proj / ".forgeflow/tasks/DEV-001.validation.json", {
            "task_id": "DEV-001", "validator": "validation-agent", "status": status,
            "checks": [{"id": "C1", "criterion_id": "AC-01", "status": crit_status, "severity": "major",
                        "evidence": "pytest exit 0", "command": "pytest", "exit_code": 0}],
            "hard_blockers": list(blockers), "tests_not_run": [], "recommended_corrections": [], "injection_flags": []})

    def test_scope_check_separates_user_changes(self):
        (self.proj / "src/app.py").write_text("print('changed')\n")
        r = json.loads(self.ff("scope-check").stdout)
        self.assertEqual(r["forgeflow_changed"], ["src/app.py"])
        self.assertEqual(r["preexisting_untouched"], ["notes.txt"])
        (self.proj / "README.md").write_text("sneaky\n")
        r = self.ff("scope-check", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("README.md", json.loads(r.stdout)["violations"])

    def test_gate_pass(self):
        self.ff("validate")
        self.report()
        self.assertEqual(json.loads(self.ff("gate", "DEV-001").stdout)["decision"], "pass")

    def test_gate_revision_cap_then_escalate(self):
        self.report(status="fail", crit_status="failed",
                    blockers=[{"code": "required_test_failed", "detail": "pytest exit 1"}])
        decisions = [json.loads(self.ff("gate", "DEV-001", ok=False).stdout)["decision"] for _ in range(3)]
        self.assertEqual(decisions, ["revise", "revise", "escalate"])

    def test_gate_rejects_pass_without_required_criterion(self):
        self.report(status="pass", crit_status="not_run")
        out = json.loads(self.ff("gate", "DEV-001", ok=False).stdout)
        self.assertEqual(out["decision"], "revise")
        self.assertTrue(any("AC-01" in r for r in out["reasons"]))

    def test_validation_input_excludes_producer_report(self):
        (self.proj / "src/app.py").write_text("print('changed')\n")
        L.write_json(self.proj / ".forgeflow/tasks/DEV-001.report.json", {"summary": "I SWEAR IT WORKS"})
        path = Path(self.ff("validation-input", "DEV-001").stdout.strip())
        text = path.read_text()
        self.assertIn("AC-01", text)
        self.assertIn("src/app.py", text)
        self.assertNotIn("I SWEAR IT WORKS", text)

    def test_validate_detects_shared_file_ownership_and_cycles(self):
        c2 = dict(self.contract, task_id="DEV-002", dependencies=["DEV-001"])
        L.write_json(self.proj / ".forgeflow/tasks/DEV-002.contract.json", c2)
        c1 = dict(self.contract, dependencies=["DEV-002"])
        L.write_json(self.proj / ".forgeflow/tasks/DEV-001.contract.json", c1)
        out = self.ff("validate", ok=False).stdout
        self.assertIn("both own", out)
        self.assertIn("cycle", out)

    def test_status_flags_resume(self):
        out = self.ff("status").stdout
        self.assertIn("RESUME", out)


if __name__ == "__main__":
    unittest.main()
