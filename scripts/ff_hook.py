"""ForgeFlow enforcement hooks.

  python ff_hook.py pre      PreToolUse guard (scope, gated commands, secrets, mode)
  python ff_hook.py prompt   UserPromptSubmit: records `approve|reject|revoke APR-NNN`

Inactive (no-op) unless the session's cwd is inside a project with an active
.forgeflow/project.json. Exit code 2 = block; stderr is shown to Claude.
Fails closed: an internal error inside an active project blocks the tool call.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ff_lib as L  # noqa: E402

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
UNSAFE_MODES = {"bypassPermissions", "auto", "dontAsk"}

# Commands that always need a matching approved command prefix.
GATED = [(re.compile(p, re.I), why) for p, why in [
    (r"\bgit\s+(push|commit|merge|rebase|reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|restore\b|branch\s+-D|tag\b|stash\s+(drop|clear))", "git history / remote change"),
    (r"\bgh\s+(pr\s+(create|merge|close)|release|repo\s+(create|delete)|issue\s+(create|comment))", "GitHub write"),
    (r"\b(npm|pnpm|yarn|bun)\s+(i|install|add|remove|uninstall|update|upgrade|publish|unpublish)\b", "package install/publish"),
    (r"\b(pip|pip3|uv|poetry|pipx|conda)\s+(install|add|remove|uninstall|pip\s+install)\b", "package install"),
    (r"\b(cargo\s+(add|install|publish)|go\s+(get|install)|gem\s+install|composer\s+(require|install))\b", "package install"),
    (r"\b(apt|apt-get|brew|choco|winget|scoop)\s+(install|remove|uninstall|upgrade)\b", "system package change"),
    (r"\b(twine\s+upload|docker\s+push|helm\s+(install|upgrade)|kubectl\s+(apply|delete|rollout)|terraform\s+(apply|destroy))\b", "deploy/publish"),
    (r"\b(vercel|netlify\s+deploy|firebase\s+deploy|fly\s+deploy|flyctl\s+deploy|eas\s+(submit|update|build)|serverless\s+deploy|wrangler\s+(deploy|publish))\b", "deploy"),
    (r"\b(prisma\s+migrate|alembic\s+(upgrade|downgrade)|manage\.py\s+migrate|rails\s+db:|knex\s+migrate|sequelize\s+db:migrate|flyway\s+migrate)", "database migration"),
    (r"\b(drop\s+(table|database)|truncate\s+table|delete\s+from)\b", "destructive SQL"),
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b|\bremove-item\b.*-recurse|\brd\s+/s\b|\bdel\s+/s\b|\brmdir\s+/s\b", "recursive delete"),
    (r"\bcurl\b.*(\s-X\s*(POST|PUT|PATCH|DELETE)|\s(-d|--data\S*|-F|--form)\s)|\binvoke-(webrequest|restmethod)\b.*-method\s+(post|put|patch|delete)", "sends data to an external service"),
    (r"\b(setx|reg\s+(add|delete)|set-itemproperty|netsh|chmod\s+-R|chown\s+-R)\b", "system/environment change"),
]]

PROTECTED = re.compile(r"\.forgeflow[\\/]+approvals|approval\.key", re.I)

SECRETS = [re.compile(p) for p in [
    r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    r"\bsk-[A-Za-z0-9_-]{20,}", r"\bxox[abprs]-[A-Za-z0-9-]{10,}", r"\bAIza[0-9A-Za-z_-]{35}",
    r"\bglpat-[A-Za-z0-9_-]{20}",
]]


# Per-role write limits. Hooks receive `agent_type` inside subagents and
# in-process teammates (plugin agents may arrive namespaced: "forgeflow:<name>").
# None = may also write inside approved scope; a list = only these .forgeflow globs.
REPORT = ".forgeflow/tasks/*.report.json"
ROLE_WRITES = {
    "research-agent": [".forgeflow/evidence/**", REPORT],
    "strategy-agent": [".forgeflow/plans/**", REPORT],
    "validation-agent": [],
    "release-agent": [".forgeflow/artifacts/**", ".forgeflow/reports/**", REPORT],
    "production-agent": None,
    "optimization-agent": None,
}
SPECIALIST_STATE = [".forgeflow/evidence/**", ".forgeflow/plans/**", ".forgeflow/artifacts/**",
                    ".forgeflow/reports/**", REPORT]
LEAD_ONLY = [".forgeflow/project.json", ".forgeflow/tasks/*.contract.json",
             ".forgeflow/tasks/*.validation.json", ".forgeflow/proposals/**", ".forgeflow/validation/**"]


def role_of(data: dict) -> str | None:
    """None for the lead (no agent_type). Otherwise the bare agent name."""
    t = data.get("agent_type")
    if not t:
        return None
    return str(t).split(":")[-1]


def block(msg: str) -> None:
    sys.stderr.write(f"ForgeFlow guard: {msg}\n")
    sys.exit(2)


def segments(cmd: str) -> list[str]:
    return [s.strip() for s in re.split(r"&&|\|\||;|\||\r?\n", cmd) if s.strip()]


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def allowed_outside_root(path: str) -> bool:
    p = Path(path).resolve()
    for base in (Path(tempfile.gettempdir()).resolve(), (Path.home() / ".claude" / "plans").resolve()):
        try:
            p.relative_to(base)
            return True
        except ValueError:
            pass
    return False


def written_text(tool: str, ti: dict) -> str:
    if tool == "Write":
        return ti.get("content", "") or ""
    if tool == "Edit":
        return ti.get("new_string", "") or ""
    if tool == "MultiEdit":
        return "\n".join(e.get("new_string", "") for e in ti.get("edits", []))
    if tool == "NotebookEdit":
        return ti.get("new_source", "") or ""
    return ""


def pre(data: dict, root: Path) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    mode = data.get("permission_mode")

    if tool not in WRITE_TOOLS | SHELL_TOOLS | {"Read", "Grep", "Glob"}:
        return

    # The approval directory and signing key are off-limits to every tool.
    probe = json.dumps(ti)
    if PROTECTED.search(probe):
        block("the approvals directory and approval key can only be written by the user's "
              "`approve APR-NNN` message. Read .forgeflow/proposals/ instead.")

    if tool in {"Read", "Grep", "Glob"}:
        return

    if mode in UNSAFE_MODES:
        block(f"permission mode '{mode}' disables human approval. Switch to default or plan "
              "mode before ForgeFlow writes files or runs commands.")

    if tool in WRITE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        rel = L.rel_to_root(root, path)
        if rel is None:
            if allowed_outside_root(path):
                return
            block(f"'{path}' is outside the ForgeFlow project. Writes outside the project are not allowed.")
        n = L.norm(rel)
        role = role_of(data)
        in_state = n.startswith(L.STATE + "/")
        if role is not None:
            if in_state and L.matches_any(rel, LEAD_ONLY):
                block(f"'{rel}' belongs to the lead (single writer). Send your result to the lead instead.")
            limits = ROLE_WRITES.get(role, SPECIALIST_STATE)
            if limits is not None:
                if not L.matches_any(rel, limits):
                    block(f"the {role} may only write {limits or 'nothing; return your report as a message'}. "
                          f"'{rel}' is not allowed for this role.")
                in_state = True  # role-limited writes are always .forgeflow/ paths
            elif in_state and not L.matches_any(rel, SPECIALIST_STATE):
                block(f"'{rel}' is not a specialist-writable state path.")
        if in_state:
            text = written_text(tool, ti)
            if any(p.search(text) for p in SECRETS):
                block("this content looks like it contains a secret (API key, token or private key). "
                      "Refer to secrets by location only; never copy them into .forgeflow/.")
            return
        if L.matches_any(rel, L.approved_files(root)):
            return
        block(f"'{rel}' is outside every approved scope. Stop and send an approval_request; the "
              "lead must run `ff.py propose --action scope_extension ...` and the user must approve it.")

    if tool in SHELL_TOOLS:
        cmd = ti.get("command", "") or ""
        prefixes = [squash(c) for c in L.approved_commands(root)]
        for seg in segments(cmd):
            for rx, why in GATED:
                if rx.search(seg) and not any(squash(seg).startswith(p) for p in prefixes if p):
                    block(f"`{seg}` is gated ({why}) and no active approval lists it. "
                          "Propose it with `ff.py propose --commands \"<exact prefix>\"` and wait "
                          "for the user to type `approve APR-NNN`.")


PROMPT_RX = re.compile(r"^\s*(approve|reject|revoke)\s+(APR-\d{3,})\b", re.I)


def prompt(data: dict, root: Path) -> None:
    text = data.get("prompt", "") or ""
    m = PROMPT_RX.match(text)
    if not m:
        return
    verb, apr = m.group(1).lower(), m.group(2).upper()
    prop_path = L.ff(root) / "proposals" / f"{apr}.json"
    appr_path = L.ff(root) / "approvals" / f"{apr}.json"

    if verb == "revoke":
        if not appr_path.is_file():
            print(f"ForgeFlow: {apr} has no approval to revoke.")
            return
        rec = L.load_json(appr_path)
        rec.update(status="revoked", decided_at=L.now(), user_statement=text[:500])
        rec["signature"] = L.sign(rec)
        L.write_json(appr_path, rec)
        print(f"ForgeFlow: the user REVOKED {apr}. Its scope is no longer allowed. Stop work that depends on it.")
        return

    if not prop_path.is_file():
        print(f"ForgeFlow: the user typed '{verb} {apr}' but no proposal {apr} exists. Nothing was recorded.")
        return
    proposal = L.load_json(prop_path)
    errs = L.validate(proposal, L.schema("approval"))
    if errs or proposal.get("status") != "proposed":
        print(f"ForgeFlow: proposal {apr} is invalid or not in 'proposed' state; nothing recorded. {errs[:3]}")
        return
    rec = dict(proposal)
    rec.update(status="approved" if verb == "approve" else "rejected", granted_by="user",
               decided_at=L.now(), user_statement=text[:500],
               proposal_sha256=L.sha256_file(prop_path))
    rec["signature"] = L.sign(rec)
    L.write_json(appr_path, rec)
    scope = proposal["scope"]
    print(f"ForgeFlow: the user {rec['status'].upper()} {apr} ({proposal['action']}). "
          f"Files: {scope['files'] or 'none'}. Commands: {scope['commands'] or 'none'}. "
          "This covers only this scope.")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    root = L.find_root(data.get("cwd") or ".")
    if root is None:
        return
    try:
        project = L.load_json(L.ff(root) / "project.json")
        if project.get("status") in L.INACTIVE_STATUSES:
            return
        {"pre": pre, "prompt": prompt}.get(mode, lambda d, r: None)(data, root)
    except SystemExit:
        raise
    except Exception as e:  # fail closed inside an active project
        if mode == "pre":
            block(f"internal error ({type(e).__name__}: {e}); blocking to stay safe. Run `ff.py doctor`.")
        print(f"ForgeFlow hook error: {e}")


if __name__ == "__main__":
    main()
