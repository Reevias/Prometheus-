import sys

import pytest

from prometheus import sandbox
from prometheus.orchestrator import front_door


@pytest.mark.skipif(sys.platform != "win32" or sys.prefix == sys.base_prefix, reason="Windows venv")
def test_windows_venv_runs_with_its_packages(tmp_path):
    from pathlib import Path
    result = sandbox.run_locked(
        "import sys, packaging\nprint(sys.executable)\nprint(sys.flags.isolated)\nprint(packaging.__file__)", tmp_path)
    assert result.ok, result.output
    executable, isolated, package = result.output.splitlines()
    assert Path(executable) == Path(sys._base_executable) and isolated == "1"
    assert Path(package).is_relative_to(sys.prefix)
