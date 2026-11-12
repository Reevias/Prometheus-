"""Test suite verifying integration of M1 primitives into Services and build_services."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import pytest

from prometheus.actions.atomic_fs import AtomicFiles
from prometheus.actions.terminal import AsyncTerminal
from prometheus.app import Services, build_services
from prometheus.config import Settings
from prometheus.models.fake import FakeProvider


def test_services_dataclass_fields():
    """Verify Services dataclass has atomic_files and terminal with None defaults."""
    assert "atomic_files" in Services.__dataclass_fields__
    assert "terminal" in Services.__dataclass_fields__


def test_build_services_wires_m1_primitives(tmp_path: Path):
    """Verify build_services properly constructs and attaches AtomicFiles and AsyncTerminal."""
    settings = Settings(budget_usd=10.0)
    home = tmp_path / "home"
    home.mkdir()

    fake = FakeProvider(["ok"], name="ollama", is_local=True)
    services = build_services(settings, providers={"ollama": fake}, home=home)

    assert isinstance(services.atomic_files, AtomicFiles)
    assert isinstance(services.terminal, AsyncTerminal)
    assert services.atomic_files.policy is services.policy
    assert services.terminal.policy is services.policy

    # Verify backward compatibility of existing critical services
    assert services.files is not None
    assert services.journal is not None
    assert services.policy is not None
    assert services.store is not None
    assert services.router is not None
    assert services.orchestrator is not None


def test_services_primitives_functional(tmp_path: Path):
    """Verify operations through services.atomic_files and services.terminal succeed."""
    settings = Settings(budget_usd=10.0)
    home = tmp_path / "home"
    home.mkdir()
    project = home / "my_project"
    project.mkdir()

    fake = FakeProvider(["ok"], name="ollama", is_local=True)
    services = build_services(settings, providers={"ollama": fake}, home=home)

    # 1. Atomic file edit and read
    test_file = project / "app.txt"
    test_file.write_text("Hello Prometheus 2.0\n", encoding="utf-8")

    edit_res = services.atomic_files.edit_exact(
        test_file,
        target="Prometheus 2.0",
        replacement="Prometheus 3.0",
        root=project,
    )
    assert edit_res.status == "done"

    read_res = services.atomic_files.read_window(test_file, start_line=1, line_count=5, root=project)
    assert "Hello Prometheus 3.0" in read_res.content

    # 2. Terminal execution
    async def run():
        return await services.terminal.execute(
            [sys.executable, "-c", "print('SERVICES_INTEGRATION_OK')"],
            cwd=project,
        )

    term_res = asyncio.run(run())
    assert term_res.exit_code == 0
    assert "SERVICES_INTEGRATION_OK" in term_res.stdout
