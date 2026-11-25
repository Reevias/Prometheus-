"""Finds background work started outside the job tracker (M5 5.1.8).

Counted per call, not per file: a file in the allow-list may start only as many raw threads/tasks as it has reasons
listed, so a new one added to an allowed file still fails the test (M5 review R4).
"""
from __future__ import annotations

import ast
from pathlib import Path

from prometheus.jobs.allowlist import ALLOWED_RAW_THREADS_AND_TASKS

THREAD_LIKE = {"Thread", "Timer", "ThreadPoolExecutor", "ProcessPoolExecutor"}
LOOP_GETTERS = {"get_event_loop", "get_running_loop", "new_event_loop"}


def _loopish(node: ast.expr) -> bool:
    """`asyncio`, `loop`, `self._loop`, `asyncio.get_event_loop()` ... (a database's create_task is not a task)."""
    if isinstance(node, ast.Name):
        return node.id == "asyncio" or node.id.lower().endswith("loop")
    if isinstance(node, ast.Attribute):
        return node.attr.lower().endswith("loop")
    if isinstance(node, ast.Call):
        f = node.func
        return (isinstance(f, ast.Attribute) and f.attr in LOOP_GETTERS) or (isinstance(f, ast.Name) and f.id in LOOP_GETTERS)
    return False


def raw_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, what) for every raw thread, timer, pool, task or Thread subclass in one module."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("threading", "asyncio", "concurrent.futures", "_thread"):
            for a in node.names:
                if a.name in THREAD_LIKE | {"create_task", "ensure_future", "start_new_thread"}:
                    aliases[a.asname or a.name] = a.name
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
                if aliases.get(name, name) == "Thread" and (isinstance(base, ast.Attribute) or name in aliases):
                    found.append((node.lineno, "subclass of threading.Thread"))
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in aliases:
            found.append((node.lineno, f"raw {aliases[f.id]}"))
        elif isinstance(f, ast.Attribute):
            if f.attr in THREAD_LIKE and isinstance(f.value, (ast.Name, ast.Attribute)):
                found.append((node.lineno, f"raw {f.attr}"))
            elif f.attr in ("create_task", "ensure_future") and _loopish(f.value):
                found.append((node.lineno, f"raw {f.attr}"))
            elif f.attr == "start_new_thread":
                found.append((node.lineno, "raw start_new_thread"))
    return sorted(found)


def find_untracked(root_dir: Path, allow: dict | None = None) -> list[str]:
    allowed = ALLOWED_RAW_THREADS_AND_TASKS if allow is None else allow
    problems: list[str] = []
    for py_file in sorted((root_dir / "prometheus").rglob("*.py")):
        rel = py_file.relative_to(root_dir).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))   # a broken file fails loudly
        calls = raw_calls(tree)
        extra = calls[len(allowed.get(rel, [])):]
        problems += [f"{rel}:{line} - {what} (not tracked and not in prometheus/jobs/allowlist.py)" for line, what in extra]
    return problems
