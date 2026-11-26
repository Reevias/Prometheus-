"""M6 6.2 item 6: the Render files (cloud/render/) and the plain-words guide. Docker itself isn't run here: the same
start command is run in a clean environment that has only requirements.txt, which proves the dependency list."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "cloud" / "render"
GUIDE = ROOT / "docs" / "cloud-setup.md"


def text(name: str) -> str:
    return (RENDER / name).read_text(encoding="utf-8")


def test_the_files_exist():
    for name in ("Dockerfile", "render.yaml", "requirements.txt"):
        assert (RENDER / name).is_file(), name


# -- Dockerfile ----------------------------------------------------------------------------------------------------------
def test_the_dockerfile_runs_only_the_cloudroom_server_as_a_normal_user():
    d = text("Dockerfile")
    assert re.search(r"^FROM python:3\.14", d, re.M)
    assert "prometheus.runtime.cloudroom_server:app_from_env" in d and "--factory" in d
    assert "--host 0.0.0.0" in d and "${PORT" in d                       # Render tells it which port
    assert re.search(r"^USER \S+", d, re.M) and "USER root" not in d
    assert "--reload" not in d


def test_the_image_gets_only_the_code_and_no_secrets():
    d = text("Dockerfile")
    assert not re.search(r"^COPY \. ", d, re.M)                          # not the whole folder (no .git, no data)
    assert re.search(r"^COPY prometheus", d, re.M)
    assert not re.search(r"(TOKEN|PASSWORD|SECRET|KEY)\s*=\s*\S", d)    # nothing secret is baked in
    assert "CLOUDROOM_TOKEN" not in d.replace("CLOUDROOM_TOKEN_FILE", "")


# -- render.yaml -------------------------------------------------------------------------------------------------------------
def test_render_yaml_asks_for_the_free_docker_web_service_with_a_health_check():
    y = text("render.yaml")
    assert re.search(r"type:\s*web", y) and re.search(r"runtime:\s*docker", y) and re.search(r"plan:\s*free", y)
    assert re.search(r"healthCheckPath:\s*/v1/health", y)
    assert "dockerfilePath: ./cloud/render/Dockerfile" in y and re.search(r"dockerContext:\s*\.", y)
    assert re.search(r"autoDeploy:\s*false", y)                          # the owner decides when it updates


def test_the_token_is_never_in_the_file_only_asked_for_in_render():
    y = text("render.yaml")
    block = y[y.index("CLOUDROOM_TOKEN"):]
    assert re.match(r"CLOUDROOM_TOKEN\s*\n?\s*sync:\s*false", block) or "sync: false" in block.split("\n", 3)[1]
    assert not re.search(r"CLOUDROOM_TOKEN\s*\n?\s*value:", y)


def test_requirements_use_the_same_versions_as_the_project():
    req = text("requirements.txt")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    names = [l.split(">=")[0].strip() for l in req.splitlines() if l.strip() and not l.startswith("#")]
    assert {"fastapi", "uvicorn", "psutil"} <= set(names)
    for line in req.splitlines():
        if line.strip() and not line.startswith("#"):
            assert f'"{line.strip()}"' in pyproject, f"{line} isn't a dependency line of pyproject.toml"


# -- it really starts with only those packages ------------------------------------------------------------------------
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(sys.platform == "win32", reason="the Render image is Linux")
def test_the_start_command_works_with_only_requirements_txt(tmp_path):
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    got = subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(RENDER / "requirements.txt")],
                         capture_output=True, text=True)
    if got.returncode != 0:
        pytest.skip(f"can't install packages here: {got.stderr[-200:]}")
    cmd = re.search(r"^CMD\s+(\[.*\])\s*$", text("Dockerfile"), re.M)
    assert cmd, "the Dockerfile needs a CMD in the exec form"
    import json

    argv = json.loads(cmd.group(1))
    port = free_port()
    env = {"PATH": f"{venv / 'bin'}:/usr/bin:/bin", "PORT": str(port), "CLOUDROOM_TOKEN": "t" * 40,
           "CLOUDROOM_DIR": str(tmp_path / "work"), "CLOUDROOM_TOKEN_FILE": str(tmp_path / "none"),
           "PYTHONPATH": str(ROOT), "HOME": str(tmp_path)}
    proc = subprocess.Popen(argv, env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        import urllib.error
        import urllib.request

        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=2) as r:
                    assert r.status == 200
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                if proc.poll() is not None:
                    pytest.fail(f"the server stopped: {proc.stdout.read().decode()[-500:]}")
                time.sleep(0.2)
        else:
            pytest.fail("the server never answered")
        with pytest.raises(urllib.error.HTTPError) as refused:                  # anything unsigned is refused
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/tasks", data=b"{}", method="POST"),
                                   timeout=5)
        assert refused.value.code in (401, 403)
    finally:
        proc.terminate()
        proc.wait(10)


# -- the guide ---------------------------------------------------------------------------------------------------------------
def test_the_guide_walks_through_render_and_says_the_limits_honestly():
    g = GUIDE.read_text(encoding="utf-8")
    assert "Render" in g and "render.yaml" in g and "cloud/render" in g
    for fact in ("15 minutes", "750", "wiped", "512 MB", "every 10 minutes", "no card", "CLOUDROOM_TOKEN"):
        assert fact in g, fact
    assert "never kept only" in g.lower() or "nothing is kept only" in g.lower()
    assert "NOT VERIFIED" in g                                              # what wasn't tried is said


def test_the_old_oracle_steps_are_gone():
    g = GUIDE.read_text(encoding="utf-8")
    assert "Create instance" not in g and "VM.Standard.A1.Flex" not in g and "ssh -i" not in g
    assert "Ampere" not in g


def test_the_guide_says_what_the_render_files_do_not_do_yet():
    g = GUIDE.read_text(encoding="utf-8").lower()
    assert "reminders" in g and "not" in g
    assert "cloud copy" in g and ("does not run" in g or "doesn't run" in g or "not part of" in g)
