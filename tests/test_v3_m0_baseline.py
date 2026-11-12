"""M0 baseline: the facts in docs/m0 come from the code, and every imported package is declared."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _baseline():
    spec = importlib.util.spec_from_file_location("m0_baseline", ROOT / "scripts" / "m0_baseline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_imported_package_is_declared():
    # This is how the missing filelock (needed by the M1 file tools) would have been caught before a fresh install.
    assert _baseline().undeclared() == []


def test_m0_documents_exist_and_were_generated():
    for name in ("CODE_INVENTORY.md", "ARCHITECTURE_MAP.md", "DEPENDENCY_MATRIX.md", "TEST_BASELINE.md",
                 "RISK_REGISTER.md"):
        text = (ROOT / "docs" / "m0" / name).read_text(encoding="utf-8")
        assert len(text) > 200, name
    assert "NOT DECLARED" not in (ROOT / "docs" / "m0" / "DEPENDENCY_MATRIX.md").read_text(encoding="utf-8")


def test_inventory_lists_the_m1_primitives():
    inv = (ROOT / "docs" / "m0" / "CODE_INVENTORY.md").read_text(encoding="utf-8")
    for module in ("prometheus/actions/atomic_fs.py", "prometheus/actions/terminal.py", "prometheus/safety/paths.py",
                   "prometheus/resume.py", "prometheus/safety/secrets.py"):
        assert f"`{module}`" in inv


def test_resume_worker_stops_cleanly():
    import threading
    from types import SimpleNamespace

    from prometheus import resume

    r = resume.Resumer(SimpleNamespace())
    before = set(threading.enumerate())
    r.start()
    mine = [t for t in threading.enumerate() if t not in before and t.name == "prometheus-resumer"]
    assert len(mine) == 1
    r.stop()
    mine[0].join(3)
    assert not mine[0].is_alive()
