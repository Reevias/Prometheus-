"""Lint test enforcing that no untracked raw threads or asyncio tasks exist (Item 5.1.8)."""
from __future__ import annotations

import ast
from pathlib import Path
import pytest

from prometheus.jobs.allowlist import ALLOWED_RAW_THREADS_AND_TASKS


def find_untracked_threads_and_tasks(root_dir: Path) -> list[str]:
    violations: list[str] = []
    prom_dir = root_dir / "prometheus"

    for py_file in sorted(prom_dir.rglob("*.py")):
        rel_path = py_file.relative_to(root_dir).as_posix()
        if rel_path in ALLOWED_RAW_THREADS_AND_TASKS:
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue

        thread_alias = None
        task_alias = None

        # Check imports to see if Thread or create_task were imported directly
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "threading":
                    for alias in node.names:
                        if alias.name == "Thread":
                            thread_alias = alias.asname or "Thread"
                elif node.module == "asyncio":
                    for alias in node.names:
                        if alias.name == "create_task":
                            task_alias = alias.asname or "create_task"

        # Walk AST to find calls to threading.Thread or asyncio.create_task
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            is_raw_thread = False
            is_raw_task = False

            # threading.Thread(...)
            if (isinstance(func, ast.Attribute) and func.attr == "Thread"
                    and isinstance(func.value, ast.Name) and func.value.id == "threading"):
                is_raw_thread = True
            # direct Thread(...) if imported
            elif isinstance(func, ast.Name) and func.id == thread_alias:
                is_raw_thread = True
            # asyncio.create_task(...)
            elif (isinstance(func, ast.Attribute) and func.attr == "create_task"
                  and isinstance(func.value, ast.Name) and func.value.id == "asyncio"):
                is_raw_task = True
            # direct create_task(...) if imported
            elif isinstance(func, ast.Name) and func.id == task_alias:
                is_raw_task = True

            if is_raw_thread:
                violations.append(f"{rel_path}:{node.lineno} - raw threading.Thread invocation")
            elif is_raw_task:
                violations.append(f"{rel_path}:{node.lineno} - raw asyncio.create_task invocation")

    return violations


def test_no_untracked_raw_threads_or_tasks():
    root = Path(__file__).resolve().parent.parent
    violations = find_untracked_threads_and_tasks(root)
    assert not violations, (
        f"Found {len(violations)} untracked raw thread/task calls outside allow-list:\n"
        + "\n".join(violations)
    )
