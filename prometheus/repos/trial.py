"""Sandbox test-run before a skill is switched on (M4.6).

`trial(skill_folder)` reads the skill's manifest.json (docs/m4/MANIFEST.md) and:
  1. checks every listed file's sha256 against the manifest, and that the needed Python packages are installed: any
     mismatch fails the trial before a single script runs;
  2. runs each script once through prometheus/sandbox.py (the locked-down Python, no internet, only its own scratch
     folder) with its sample `inputs` written as inputs.json and within its `timeout`;
  3. does NOT run a script whose skill has the `network` permission: it is marked "needs the internet" and the owner
     is asked at its first real use.
A skill may be switched on only when `TrialResult.passed`. A pass only counts while the scripts and the manifest are
exactly as they were tested (`still_valid`): change one byte and a new trial (and a new yes) is needed.
Scripts are never run outside the sandbox, and no sandbox rule is loosened here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from prometheus import sandbox

KV = "skill_trials"
MAX_MANIFEST = 200_000
DETAIL_CHARS = 4000
NEEDS_INTERNET = "It needs the internet: you'll be asked at first real use."


@dataclass
class ScriptTrial:
    file: str
    status: str                    # "passed", "failed" or "skipped"
    reason: str = ""               # one plain sentence (empty when it passed)
    details: str = ""              # what the script printed or the raw error: shown only behind "Show details"


@dataclass
class TrialResult:
    passed: bool
    reason: str = ""               # the first thing that went wrong, as a plain sentence ("" when it passed)
    scripts: list[ScriptTrial] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)      # what was tested: manifest.json and each script
    made: float = 0.0

    def still_valid(self, skill_folder: Path) -> bool:
        """True only while the manifest and scripts are byte-for-byte what was tested."""
        if not self.passed or not self.hashes:
            return False
        folder = Path(skill_folder)
        return all(_sha_of(folder, rel) == sha for rel, sha in self.hashes.items())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TrialResult":
        return cls(bool(data.get("passed")), str(data.get("reason", "")),
                   [ScriptTrial(**s) for s in data.get("scripts", []) if isinstance(s, dict)],
                   {str(k): str(v) for k, v in (data.get("hashes") or {}).items()}, float(data.get("made", 0.0)))


def _plain_inside(folder: Path, rel: str) -> Path | None:
    """The file `rel` inside the skill folder if it is a plain file (no link, nothing outside the folder)."""
    try:
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
            return None
        path = folder / rel
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            return None
        return path
    except (OSError, ValueError):
        return None


def _sha_of(folder: Path, rel: str) -> str:
    path = _plain_inside(folder, rel)
    if path is None:
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _seconds(n: int) -> str:
    return f"{n} second{'s' if n != 1 else ''}"


def _fail(reason: str, scripts: list[ScriptTrial] | None = None, hashes: dict | None = None) -> TrialResult:
    return TrialResult(False, reason, scripts or [], hashes or {}, time.time())


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "it gave no reason")[:200]


def _check_files(folder: Path, manifest: dict) -> str:
    """'' when every file in the manifest is there and unchanged; otherwise a plain sentence."""
    files = manifest.get("files")
    if not isinstance(files, dict):
        return "The skill's manifest doesn't list its files, so it can't be tested."
    listed = dict(files)
    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict) or not isinstance(script.get("file"), str):
            return "The skill's manifest has a script entry that can't be read."
        if listed.get(script["file"]) != script.get("sha256"):
            return f"{script['file']} isn't listed in the manifest with the same fingerprint, so it wasn't run."
        listed[script["file"]] = script["sha256"]
    for rel, sha in listed.items():
        if _sha_of(folder, rel) != sha:
            return f"{rel} changed since the skill was made, so nothing was run."
    return ""


def trial(skill_folder: Path, base: Path | None = None) -> TrialResult:
    """Test-run a skill's scripts in the sandbox. `base` is where scratch folders go (default: <data>/skill_trials)."""
    folder = Path(skill_folder)
    manifest_path = _plain_inside(folder, "manifest.json")
    try:
        if manifest_path is None or manifest_path.stat().st_size > MAX_MANIFEST:
            return _fail("The skill has no readable manifest.json, so it can't be tested.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _fail("The skill's manifest.json can't be read, so it can't be tested.")
    if not isinstance(manifest, dict):
        return _fail("The skill's manifest.json can't be read, so it can't be tested.")
    problem = _check_files(folder, manifest)
    if problem:                                              # before anything runs
        return _fail(problem)
    for package in manifest.get("packages") or []:
        top = str(package).split(".")[0]
        if not top.isidentifier() or importlib.util.find_spec(top) is None:
            return _fail(f"It needs the Python package {top}, which isn't installed.")
    scripts = manifest.get("scripts") or []
    hashes = {"manifest.json": _sha_of(folder, "manifest.json")}
    hashes.update({s["file"]: s["sha256"] for s in scripts})
    perms = manifest.get("permissions") or []
    if base is None:
        from prometheus.config import data_dir

        base = data_dir() / "skill_trials"
    base = Path(base)
    out: list[ScriptTrial] = []
    for script in scripts:
        rel = script["file"]
        if "network" in perms:
            out.append(ScriptTrial(rel, "skipped", NEEDS_INTERNET))
            continue
        out.append(_run_one(folder, base, rel, script))
    failed = [s for s in out if s.status == "failed"]
    reason = f"{failed[0].file}: {failed[0].reason}" if failed and len(scripts) > 1 else (failed[0].reason if failed else "")
    return TrialResult(not failed, reason, out, hashes, time.time())


def _run_one(folder: Path, base: Path, rel: str, script: dict) -> ScriptTrial:
    try:
        timeout = int(script.get("timeout"))
    except (TypeError, ValueError):
        timeout = sandbox.TIME_LIMIT
    timeout = max(1, min(timeout, sandbox.TIME_LIMIT))       # never longer than the sandbox's own limit
    inputs = script.get("inputs") if isinstance(script.get("inputs"), dict) else {}
    code = _plain_inside(folder, rel).read_text(encoding="utf-8")
    scratch = sandbox.new_folder(base)
    try:
        (scratch / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
        result = sandbox.run_locked(code, scratch, network=False, timeout=timeout)
    except OSError as e:
        return ScriptTrial(rel, "failed", f"The sandbox couldn't start the script: {e.strerror or e}.", str(e)[:DETAIL_CHARS])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    details = result.output.strip()[:DETAIL_CHARS]
    if result.timed_out:
        return ScriptTrial(rel, "failed", f"It took longer than {_seconds(timeout)}, so it was stopped.", details)
    if result.needs_network:
        return ScriptTrial(rel, "failed", "It tried to use the internet without permission.", details)
    if not result.ok:
        return ScriptTrial(rel, "failed", f"The script stopped with an error: {_last_line(result.output)}", details)
    return ScriptTrial(rel, "passed", "", details or "(it printed nothing)")


# -- remembering results (for the Candidates card and the switch-on check) --------------------------------------------
def save_result(store, name: str, result: TrialResult) -> None:
    data = store.kv_get(KV, {})
    data = data if isinstance(data, dict) else {}
    data[name] = result.to_dict()
    store.kv_set(KV, data)


def load_result(store, name: str) -> TrialResult | None:
    data = store.kv_get(KV, {})
    got = data.get(name) if isinstance(data, dict) else None
    try:
        return TrialResult.from_dict(got) if isinstance(got, dict) else None
    except (TypeError, ValueError):
        return None
