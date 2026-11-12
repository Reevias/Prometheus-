from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("PROMETHEUS_HOME", str(data))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return SimpleNamespace(home=home, data=data)


@pytest.fixture(autouse=True)
def isolate_quick_weather(monkeypatch):
    """Ensure prometheus.quick.WEATHER starts at None for each test and resets on teardown."""
    from prometheus import quick
    monkeypatch.setattr(quick, "WEATHER", None)
    yield
    quick.WEATHER = None


@pytest.fixture
def make_ctx(tmp_home):
    """Build an AgentContext with real safety pieces and the given providers."""
    from prometheus.actions.files import FileActions
    from prometheus.actions.journal import Journal
    from prometheus.agents.base import REGISTRY, AgentContext
    from prometheus.memory.store import Store
    from prometheus.models.router import ModelRouter
    from prometheus.safety.approvals import Approvals
    from prometheus.safety.budget import Budget
    from prometheus.safety.killswitch import KillSwitch
    from prometheus.safety.paths import PathPolicy
    from prometheus.safety.permissions import Permissions

    def build(providers=(), routes=None, trash=None):
        store = Store(tmp_home.data / "memory.db")
        ks = KillSwitch()
        budget = Budget(store, 5.0)
        router = ModelRouter({p.name: p for p in providers},
                             routes or {"private": [p.name for p in providers if p.is_local]}, budget, ks)
        policy = PathPolicy(home=tmp_home.home, data_dir=tmp_home.data, code_dir=tmp_home.data / "code")
        journal = Journal(tmp_home.data / "journals")
        files = FileActions(policy, journal, trash=trash or (lambda p: None))
        perms = Permissions({name: cls.tools for name, cls in REGISTRY.items()})
        chat = store.create_chat("t")
        task = store.create_task(chat, "t")
        step = store.add_step(task, "x", "x", {})
        return AgentContext(task_id=task, step_id=step, store=store, router=router, policy=policy, files=files,
                            permissions=perms, approvals=Approvals(store), killswitch=ks, home=tmp_home.home)

    return build


@pytest.fixture
def app_client(tmp_home, monkeypatch):
    """A logged-out TestClient over fully wired services with fake models."""
    from fastapi.testclient import TestClient

    from prometheus.app import build_services
    from prometheus.auth import hash_password
    from prometheus.config import Settings
    from prometheus.models.fake import FakeProvider
    from prometheus.server import create_app

    def build(providers=None, routes=None):
        providers = providers or [FakeProvider(['{"steps": []}'], name="fake")]
        settings = Settings(password_hash=hash_password("correct horse battery"),
                            routes=routes or {"plan": ["fake"], "combine": ["fake"], "research": ["fake"],
                                              "private": ["fake"]})
        services = build_services(settings, providers={p.name: p for p in providers}, home=tmp_home.home)
        client = TestClient(create_app(services), base_url="http://127.0.0.1:8000")
        return client, services

    return build


@pytest.fixture
def real_client(tmp_home, monkeypatch):
    """A TestClient over services with the real provider list (no model is actually called)."""
    from fastapi.testclient import TestClient

    from prometheus.app import build_services
    from prometheus.auth import hash_password
    from prometheus.config import Settings
    from prometheus.server import create_app

    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    settings = Settings(password_hash=hash_password("correct horse battery"), embed_model="",
                        ollama_url="http://127.0.0.1:9")
    services = build_services(settings, home=tmp_home.home)
    return TestClient(create_app(services), base_url="http://127.0.0.1:8000"), services


def login(client, password="correct horse battery"):
    resp = client.post("/login", data={"password": password}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return client.cookies.get("csrf_token")


@pytest.fixture(autouse=True)
def _time_sleep_is_restored():
    """M0 isolation guard: a test that replaces time.sleep for good (instead of with monkeypatch) breaks every test
    after it. Fail that test, and put the real one back."""
    import time as _time

    real = _time.sleep
    yield
    if _time.sleep is not real:
        _time.sleep = real
        pytest.fail("time.sleep was replaced and not restored; use monkeypatch.setattr")
