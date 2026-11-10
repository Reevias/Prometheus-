"""Tests for the v1.1 security review findings (each failed before its fix)."""
import os
import socket

import httpx
import pytest

from conftest import login

from prometheus import config
from prometheus.agents.opener import Opener, open_local_handler
from prometheus.config import data_dir, get_secret, set_secret
from prometheus.memory.store import Store
from prometheus.models.anthropic_provider import AnthropicProvider
from prometheus.models.base import PrivacyViolation, ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.models.ollama import OllamaProvider
from prometheus.models.router import ModelRouter
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch
from prometheus.safety.paths import PathBlocked, PathPolicy


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])


# R1: only plain video / repo pages open without asking
@pytest.mark.parametrize("url", [
    "https://github.com/attacker/x/releases/download/v1/setup.exe",
    "https://github.com/login/oauth/authorize?client_id=x",
    "https://www.youtube.com/redirect?q=https://evil.com",
    "https://github.com/a/b/archive/refs/heads/main.zip",
])
def test_only_plain_pages_auto_open(make_ctx, public_dns, url):
    opened = []
    ctx = make_ctx()
    result = Opener(launcher=opened.append).run("open", {"target": url}, ctx)
    assert opened == [] and result.status == "waiting_approval"


@pytest.mark.parametrize("url", ['https://github.com/ --foo "x', "https://github.com/a/b --incognito",
                                 "https://[github.com]/a/b"])
def test_links_with_odd_characters_never_open(make_ctx, public_dns, url):
    opened = []
    ctx = make_ctx()
    result = Opener(launcher=opened.append).run("open", {"target": url}, ctx)
    assert opened == [] and result.status == "failed" and ctx.store.approvals() == []


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ",
                                 "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://github.com/Reevias/Prometheus-"])
def test_normal_pages_still_open(make_ctx, url):
    opened = []
    Opener(launcher=opened.append).run("open", {"target": url}, make_ctx())
    assert opened == [url]


# R2: local files use an allowlist, and the approval shows the full path
@pytest.mark.parametrize("name", ["invoice.msix", "page.html", "disk.iso", "report.docm", "old.doc", "x.svg", "a.rdp"])
def test_only_documents_and_media_can_be_opened(tmp_home, make_ctx, name):
    docs = tmp_home.home / "Documents"
    docs.mkdir()
    (docs / name).write_text("x")
    ctx = make_ctx()
    assert Opener(launcher=lambda u: None).run("open", {"kind": "file", "target": f"Documents/{name}"}, ctx).status == "failed"
    with pytest.raises(ValueError):
        open_local_handler({"path": str(docs / name)}, ctx.policy, lambda p: None)


def test_colon_in_file_name_refused(tmp_home, make_ctx):
    ctx = make_ctx()
    with pytest.raises(ValueError):
        open_local_handler({"path": str(tmp_home.home / "evil.pdf::$DATA")}, ctx.policy, lambda p: None)


def test_folder_can_be_opened_and_card_shows_full_path(tmp_home, make_ctx, app_client):
    docs = tmp_home.home / "Documents"
    docs.mkdir()
    (docs / "notes.pdf").write_text("x")
    ctx = make_ctx()
    assert Opener(launcher=lambda u: None).run("open", {"kind": "file", "target": "Documents"}, ctx).status == "waiting_approval"
    Opener(launcher=lambda u: None).run("open", {"kind": "file", "target": "Documents/notes.pdf"}, ctx)
    summaries = [a.summary for a in ctx.store.approvals()]
    assert any(str((docs / "notes.pdf").resolve()) in s for s in summaries)


# R3: password guesses on the key endpoints are limited
def test_key_endpoint_limits_password_guesses(app_client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    data = {"name": "OPENROUTER_API_KEY", "value": "sk-or-attacker", "password": "guess"}
    codes = [client.post("/settings/keys", data=data, headers=h).status_code for _ in range(6)]
    assert codes[:5] == [403] * 5 and codes[5] == 429
    assert client.get("/settings", follow_redirects=False).status_code in (303, 401)   # logged out
    assert get_secret("OPENROUTER_API_KEY") is None
    assert any(e["action"] == "key_guess_lockout" for e in services.store.audit_log())


# R4: error pages from forms are actually shown
def test_htmx_is_told_to_show_error_responses(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/settings").text
    assert "htmx-config" in page and "responseHandling" in page and "[45].." in page


# R5: the key file is written safely and leftovers are unreadable to agents
def test_key_save_retries_and_never_leaves_a_copy(tmp_home, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(config.os, "replace", flaky)
    set_secret("OPENROUTER_API_KEY", "sk-or-test-1")
    assert get_secret("OPENROUTER_API_KEY") == "sk-or-test-1"

    def always(src, dst):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(config.os, "replace", always)
    with pytest.raises(OSError):
        set_secret("OPENROUTER_API_KEY", "sk-or-test-2")
    assert [p.name for p in data_dir().iterdir() if p.name.startswith(".env")] == [".env"]


def test_policy_blocks_any_env_file(tmp_path):
    home = tmp_path / "home"
    data = home / "data"
    data.mkdir(parents=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=tmp_path / "code")
    for name in (".env", ".env.tmp", ".env.bak", "settings.json", "settings.tmp"):
        with pytest.raises(PathBlocked):
            policy.check_read(data / name)


@pytest.mark.parametrize("value", ["abc ANTHROPIC_API_KEY=evil", "abc\x85x", "abc\x0bx", "ab c", "é" * 10])
def test_key_values_must_be_plain_printable(tmp_home, value):
    with pytest.raises(ValueError):
        set_secret("OPENROUTER_API_KEY", value)


# R6: a changed key works straight away
def test_claude_client_follows_the_current_key(tmp_home, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    made = []
    provider = AnthropicProvider(client_factory=lambda key: made.append(key) or object())
    set_secret("ANTHROPIC_API_KEY", "sk-ant-first")
    provider._get_client()
    provider._get_client()
    set_secret("ANTHROPIC_API_KEY", "sk-ant-second")
    provider._get_client()
    assert made == ["sk-ant-first", "sk-ant-second"]


def test_saving_a_key_wakes_resting_models(app_client, monkeypatch):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    services.router._resting["fake"] = (services.router.clock() + 300, "the key was rejected")
    client.post("/settings/keys", data={"name": "OPENROUTER_API_KEY", "value": "sk-or-new-key",
                                        "password": "correct horse battery"}, headers=h)
    assert services.router._resting == {}


# R7 (re-graded): an Ollama cloud model or a remote Ollama is not "local"
def test_ollama_cloud_model_is_not_local(tmp_path):
    assert OllamaProvider("qwen3.5:2b", "http://127.0.0.1:11434").is_local
    assert OllamaProvider("qwen3.5:2b", "http://localhost:11434").is_local
    assert not OllamaProvider("glm-5.3-flash:cloud", "http://127.0.0.1:11434").is_local
    assert not OllamaProvider("kimi-k3.1-pro-cloud", "http://127.0.0.1:11434").is_local
    assert not OllamaProvider("qwen3.5:2b", "http://192.168.1.9:11434").is_local
    budget = Budget(Store(tmp_path / "m.db"), 5.0)
    with pytest.raises(PrivacyViolation):
        ModelRouter({"ollama": OllamaProvider("glm-5.3-flash:cloud", "http://127.0.0.1:11434")},
                    {"private": ["ollama"]}, budget, KillSwitch())


def test_private_call_refused_if_ollama_switched_to_cloud_later(tmp_path):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": [], "message": {"content": "x"}})))
    ollama = OllamaProvider("qwen3.5:2b", "http://127.0.0.1:11434", http=http)
    router = ModelRouter({"ollama": ollama}, {"private": ["ollama"]}, Budget(Store(tmp_path / "m.db"), 5.0), KillSwitch())
    ollama.model = "glm-5.3-flash:cloud"
    with pytest.raises(PrivacyViolation):
        router.complete("private", [{"role": "user", "content": "secret"}], "s")


# minor: ":free" only counts for OpenRouter, including its default model
def test_free_suffix_only_counts_for_openrouter(tmp_path):
    budget = Budget(Store(tmp_path / "m.db"), 1.0)
    budget.record("x", "m", 1.0, 1, 1)
    paid = FakeProvider(["x"], name="anthropic", is_local=False)
    router = ModelRouter({"anthropic": paid}, {"plan": ["anthropic:claude:free"]}, budget, KillSwitch())
    with pytest.raises(Exception):
        router.complete("plan", [{"role": "user", "content": "q"}], "s")
    assert paid.calls == []
    free = FakeProvider(["ok"], name="openrouter", is_local=False)
    free.model = "vendor/model:free"
    router2 = ModelRouter({"openrouter": free}, {"plan": ["openrouter"]}, budget, KillSwitch())
    assert router2.complete("plan", [{"role": "user", "content": "q"}], "s").text == "ok"
