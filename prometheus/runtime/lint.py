"""The check that every job starts through the runtime (M6 step 6.1, item 3).

Outside prometheus/jobs/ (M5's engine) and prometheus/runtime/ (this package), code may not import or use the M5
job starters: start_job / JobRunner / GraphRunner / get_default_runner / set_default_runner from prometheus.jobs,
nor call .run_existing() or .start_job() on anything. It uses prometheus.runtime.start_job instead.
"""
from __future__ import annotations

import ast
from pathlib import Path

STARTERS = {"start_job", "JobRunner", "GraphRunner", "get_default_runner", "set_default_runner"}
CALLS = {"run_existing", "start_job"}
OWN = ("prometheus/jobs/", "prometheus/runtime/")


def _direct(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("prometheus.jobs"):
            for a in node.names:
                if a.name in STARTERS:
                    found.append((node.lineno, f"imports {a.name} from {node.module}"))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("prometheus.jobs"):
                    module_aliases.add(a.asname or a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in STARTERS and isinstance(node.value, (ast.Name, ast.Attribute)) \
                    and ast.unparse(node.value) in module_aliases:
                found.append((node.lineno, f"uses {ast.unparse(node)}"))
            if node.attr in CALLS:
                found.append((node.lineno, f"calls .{node.attr}()"))
    return sorted(set(found))


def find_direct_starts(root: Path) -> list[str]:
    problems: list[str] = []
    for py in sorted((Path(root) / "prometheus").rglob("*.py")):
        rel = py.relative_to(root).as_posix()
        if rel.startswith(OWN):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        problems += [f"{rel}:{line} - {what} (start jobs through prometheus.runtime)" for line, what in _direct(tree)]
    return problems
