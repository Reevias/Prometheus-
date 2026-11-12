"""M0 baseline facts, read from the code itself (not written by hand).

    python scripts/m0_baseline.py            # print the code inventory, architecture map and dependency matrix
    python scripts/m0_baseline.py --write    # also save them in docs/m0/

The test tests/test_v3_m0_baseline.py runs the same checks, so the facts can't silently go stale where it matters:
every third-party package the code imports must be declared in pyproject.toml.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "prometheus"

# import name -> distribution name, where they differ
IMPORT_TO_DIST = {
    "PIL": "pillow", "docx": "python-docx", "pptx": "python-pptx", "multipart": "python-multipart",
    "yaml": "pyyaml", "webview": "pywebview", "faster_whisper": "faster-whisper", "kokoro_onnx": "kokoro-onnx",
    "google": "google-genai", "youtube_transcript_api": "youtube-transcript-api", "fpdf": "fpdf2",
    "send2trash": "send2trash", "espeakng_loader": "espeakng-loader", "phonemizer": "phonemizer-fork",
    "pywebpush": "pywebpush", "rapidocr": "rapidocr", "soundfile": "soundfile",
}
# installed alongside a declared package (not imported on their own terms) or part of the platform
PROVIDED_BY = {
    "starlette": "fastapi", "pydantic": "fastapi", "markupsafe": "jinja2", "anyio": "httpx", "h11": "uvicorn",
    "win32api": "pywin32 (Windows, via pystray)", "pythoncom": "pywin32", "win32con": "pywin32", "winreg": "stdlib",
    "msvcrt": "stdlib", "_winapi": "stdlib", "fcntl": "stdlib", "termios": "stdlib", "pwd": "stdlib",
    "grp": "stdlib", "resource": "stdlib", "winsound": "stdlib", "nt": "stdlib", "posix": "stdlib",
    "py_vapid": "pywebpush", "http_ece": "pywebpush", "cffi": "sounddevice",
    "av": "faster-whisper",
}
# imported only inside an optional path that falls back or explains itself when the package is missing
OPTIONAL_UNDECLARED = {
    "rapidocr_onnxruntime": "fallback for Python up to 3.12 when rapidocr 3 isn't there (prometheus/photos.py)",
    "piper": "optional Piper laptop voice; not installed by the installer (risk register R-6)",
}


def modules() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def top_imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def declared() -> dict[str, str]:
    """distribution name (lower) -> where it is declared ('required' or the extra's name)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out = {}
    for spec in data["project"]["dependencies"]:
        out[re.split(r"[<>=!~\[ ;]", spec, maxsplit=1)[0].lower()] = "required"
    for extra, specs in data["project"].get("optional-dependencies", {}).items():
        for spec in specs:
            out.setdefault(re.split(r"[<>=!~\[ ;]", spec, maxsplit=1)[0].lower(), extra)
    out.setdefault("kokoro-onnx", "voice (installed separately, see pyproject.toml)")
    return out


def third_party() -> dict[str, set[str]]:
    """import name -> modules that import it (stdlib and prometheus itself left out)."""
    std = set(sys.stdlib_module_names) | {"__future__"}
    found: dict[str, set[str]] = {}
    for m in modules():
        for name in top_imports(m):
            if name in std or name == "prometheus" or name.startswith("_"):
                continue
            found.setdefault(name, set()).add(str(m.relative_to(ROOT)).replace("\\", "/"))
    return found


def undeclared() -> list[str]:
    dec = declared()
    missing = []
    for name in sorted(third_party()):
        if name in PROVIDED_BY or name in OPTIONAL_UNDECLARED:
            continue
        dist = IMPORT_TO_DIST.get(name, name).lower().replace("_", "-")
        if dist not in dec and dist.replace("-", "_") not in dec:
            missing.append(name)
    return missing


def first_line(path: Path) -> str:
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except SyntaxError:
        doc = ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def inventory_md() -> str:
    rows, total = [], 0
    for m in modules():
        n = sum(1 for _ in m.open(encoding="utf-8"))
        total += n
        rows.append(f"| `{m.relative_to(ROOT).as_posix()}` | {n} | {first_line(m).replace('|', '/')} |")
    head = (f"# M0 code inventory\n\nGenerated by `scripts/m0_baseline.py` at commit `{commit()}`: "
            f"{len(rows)} modules, {total} lines.\n\n| Module | Lines | What it does (its own docstring) |\n|---|---|---|\n")
    return head + "\n".join(rows) + "\n"


def architecture_md() -> str:
    groups: dict[str, list[Path]] = {}
    for m in modules():
        rel = m.relative_to(PKG)
        groups.setdefault(rel.parts[0] if len(rel.parts) > 1 else "(top level)", []).append(m)
    lines = [f"# M0 architecture map\n\nGenerated by `scripts/m0_baseline.py` at commit `{commit()}`. Entry points: "
             "`prometheus/cli.py` (`prometheus` command) → `prometheus/app.py` (`Services`: builds the store, path "
             "policy, model router, orchestrator, agents and the M1 primitives `AtomicFiles` and `AsyncTerminal`) → "
             "`prometheus/server.py` / `prometheus/web/` (FastAPI + HTMX UI) and `prometheus/desktop.py` "
             "(pywebview window).\n\nInternal imports between packages (who uses whom):\n"]
    edges: dict[str, set[str]] = {}
    for m in modules():
        src = m.relative_to(PKG).parts[0].removesuffix(".py")
        try:
            tree = ast.parse(m.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("prometheus."):
                dst = node.module.split(".")[1]
                if dst != src:
                    edges.setdefault(src, set()).add(dst)
    for pkg in sorted(groups):
        name = pkg.removesuffix(".py")
        if pkg == "(top level)":
            continue
        uses = ", ".join(sorted(edges.get(name, ()))) or "nothing else in prometheus"
        lines.append(f"- **{name}/** ({len(groups[pkg])} modules): uses {uses}")
    lines.append(f"- **top-level modules** ({len(groups.get('(top level)', []))}): see the code inventory")
    return "\n".join(lines) + "\n"


def dependency_md() -> str:
    dec = declared()
    tp = third_party()
    lines = [f"# M0 dependency matrix\n\nGenerated by `scripts/m0_baseline.py` at commit `{commit()}`.\n",
             "| Import | Package | Declared in | Used by |", "|---|---|---|---|"]
    for name in sorted(tp):
        dist = PROVIDED_BY.get(name) or IMPORT_TO_DIST.get(name, name)
        if name in OPTIONAL_UNDECLARED:
            where = "optional, not declared: " + OPTIONAL_UNDECLARED[name]
        else:
            where = "comes with " + PROVIDED_BY[name] if name in PROVIDED_BY else dec.get(
                dist.lower(), dec.get(dist.lower().replace("_", "-"), "**NOT DECLARED**"))
        users = sorted(tp[name])
        used = ", ".join(f"`{u}`" for u in users[:4]) + (f" and {len(users) - 4} more" if len(users) > 4 else "")
        lines.append(f"| `{name}` | {dist} | {where} | {used} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    docs = {"CODE_INVENTORY.md": inventory_md(), "ARCHITECTURE_MAP.md": architecture_md(),
            "DEPENDENCY_MATRIX.md": dependency_md()}
    if "--write" in sys.argv:
        out = ROOT / "docs" / "m0"
        out.mkdir(parents=True, exist_ok=True)
        for name, text in docs.items():
            (out / name).write_text(text, encoding="utf-8")
    else:
        print("\n".join(docs.values()))
    missing = undeclared()
    if missing:
        print("Imported but not declared in pyproject.toml:", ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
