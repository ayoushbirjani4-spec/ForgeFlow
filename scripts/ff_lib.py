"""Shared helpers for ForgeFlow scripts and hooks. Python 3.9+, standard library only."""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

import sys

# Windows consoles default to cp1252; never crash on non-ASCII names or messages.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PLUGIN_ROOT / "skills" / "forgeflow" / "schemas"
STATE = ".forgeflow"
SUBDIRS = ("tasks", "evidence", "plans", "artifacts", "reports", "proposals", "approvals", "validation")
DEFAULT_MAX_REVISIONS = 2
INACTIVE_STATUSES = {"completed", "cancelled"}


# ---------------------------------------------------------------- basics
def now() -> str:
    """Local time with the real UTC offset (e.g. +05:30 in India)."""
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # atomic on the same filesystem


def sha256_file(path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def find_root(start) -> Path | None:
    """Nearest ancestor of `start` that contains .forgeflow/project.json."""
    p = Path(start).resolve()
    for d in (p, *p.parents):
        if (d / STATE / "project.json").is_file():
            return d
    return None


def ff(root) -> Path:
    return Path(root) / STATE


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "project")[:40].strip("-") or "project"


def new_project_id(name: str) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    rand = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{slugify(name)}-{datetime.date.today():%Y%m%d}-{rand}"


# ---------------------------------------------------------------- paths & globs
def norm(rel: str) -> str:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lower() if os.name == "nt" else rel


def rel_to_root(root, path) -> str | None:
    """Root-relative posix path, or None when the path is outside root."""
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(root) / p
        return Path(os.path.normpath(p)).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None


def glob_to_regex(pattern: str) -> re.Pattern:
    pattern = norm(pattern)
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c)))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(rel: str, globs) -> bool:
    rel = norm(rel)
    return any(glob_to_regex(g).match(rel) for g in globs)


# ---------------------------------------------------------------- mini JSON-schema validator
def _type_ok(v, t) -> bool:
    if isinstance(t, list):
        return any(_type_ok(v, x) for x in t)
    return {
        "object": isinstance(v, dict),
        "array": isinstance(v, list),
        "string": isinstance(v, str),
        "boolean": isinstance(v, bool),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "number": isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": v is None,
    }[t]


def validate(inst, schema, path="$") -> list[str]:
    """Supports the subset ForgeFlow schemas use: type, enum, const, anyOf,
    required, properties, additionalProperties:false, items, minItems,
    minLength, maxLength, pattern, minimum, maximum."""
    errs: list[str] = []
    if "anyOf" in schema:
        if not any(not validate(inst, s, path) for s in schema["anyOf"]):
            errs.append(f"{path}: matches none of the allowed forms")
        return errs
    if "type" in schema and not _type_ok(inst, schema["type"]):
        return [f"{path}: expected {schema['type']}, got {type(inst).__name__}"]
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if isinstance(inst, str):
        if len(inst) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: {inst!r} does not match {schema['pattern']}")
    if _type_ok(inst, "number"):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if isinstance(inst, list):
        if len(inst) < schema.get("minItems", 0):
            errs.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(inst):
                errs += validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(inst, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing required field '{k}'")
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected field '{k}'")
    return errs


def schema(name: str) -> dict:
    return load_json(SCHEMA_DIR / f"{name}.schema.json")


# ---------------------------------------------------------------- approvals (signed)
def key_path() -> Path:
    return Path(os.environ.get("FORGEFLOW_KEY_DIR", Path.home() / ".forgeflow")) / "approval.key"


def _key(create: bool) -> bytes | None:
    kp = key_path()
    if kp.is_file():
        return kp.read_bytes()
    if not create:
        return None
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_bytes(secrets.token_bytes(32))
    return kp.read_bytes()


def _canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(record: dict) -> str:
    return hmac.new(_key(create=True), _canonical(record), hashlib.sha256).hexdigest()


def signature_ok(record: dict) -> bool:
    k = _key(create=False)
    if not k or not isinstance(record.get("signature"), str):
        return False
    good = hmac.new(k, _canonical(record), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, record["signature"])


def active_approvals(root) -> list[dict]:
    """Approvals the user actually granted: written by the UserPromptSubmit hook,
    HMAC-signed, status approved, and the proposal unchanged since approval."""
    out = []
    adir = ff(root) / "approvals"
    if not adir.is_dir():
        return out
    for f in sorted(adir.glob("APR-*.json")):
        try:
            rec = load_json(f)
        except (OSError, ValueError):
            continue
        if rec.get("status") != "approved" or rec.get("granted_by") != "user":
            continue
        if not signature_ok(rec):
            continue
        prop = ff(root) / "proposals" / f.name
        if sha256_file(prop) != rec.get("proposal_sha256"):
            continue
        out.append(rec)
    return out


def approved_files(root) -> list[str]:
    return [g for a in active_approvals(root) for g in a["scope"]["files"]]


def approved_commands(root) -> list[str]:
    return [c for a in active_approvals(root) for c in a["scope"]["commands"]]


# ---------------------------------------------------------------- git
def git(root, *args) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def is_git_root(root) -> bool:
    """True if root is inside a git work tree. All git queries use --relative /
    run from root, so results stay limited to root's subtree."""
    code, out = git(root, "rev-parse", "--show-toplevel")
    return code == 0 and bool(out.strip())


def changed_files(root, head: str | None) -> list[str]:
    """Paths (relative to root) changed vs `head`, plus untracked, limited to root."""
    files: set[str] = set()
    if head:
        _, out = git(root, "diff", "--name-only", "--relative", head)
    else:
        _, out = git(root, "diff", "--name-only", "--relative", "--cached")
    files.update(l for l in out.splitlines() if l.strip())
    _, out = git(root, "ls-files", "--others", "--exclude-standard")
    files.update(l for l in out.splitlines() if l.strip())
    return sorted(f for f in files if not norm(f).startswith(STATE + "/"))


def record_baseline(root) -> dict:
    if not is_git_root(root):
        return {"git": False, "head": None, "branch": None, "dirty_files": [], "recorded_at": now()}
    code, head = git(root, "rev-parse", "HEAD")
    head = head.strip() if code == 0 else None
    _, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    dirty = [{"path": p, "sha256": sha256_file(Path(root) / p)} for p in changed_files(root, head)]
    return {"git": True, "head": head, "branch": branch.strip() or None,
            "dirty_files": dirty, "recorded_at": now()}


def scope_report(root) -> dict:
    project = load_json(ff(root) / "project.json")
    base = project["baseline"]
    if not base["git"]:
        return {"verifiable": False, "reason": "not a git repository; scope cannot be verified",
                "forgeflow_changed": [], "preexisting_untouched": [], "violations": []}
    pre = {norm(d["path"]): d["sha256"] for d in base["dirty_files"]}
    allowed = approved_files(root)
    changed, untouched, violations = [], [], []
    for p in changed_files(root, base["head"]):
        n = norm(p)
        if n in pre and pre[n] == sha256_file(Path(root) / p):
            untouched.append(p)
            continue
        changed.append(p)
        if not matches_any(p, allowed):
            violations.append(p)
    return {"verifiable": True, "forgeflow_changed": changed,
            "preexisting_untouched": untouched, "violations": violations}
