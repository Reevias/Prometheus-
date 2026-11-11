import re

from prometheus.config import delete_secret, get_secret, set_secret
from prometheus.memory.store import Store
from prometheus.models.base import ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.models.router import ModelRouter
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch
from tests.conftest import login


class OneBadModel(FakeProvider):
    def complete(self, messages, system, json_schema=None, max_tokens=4096, model=None):
        if model == "typo-model":
            self.calls.append({"model": model})
            raise ProviderUnavailable("that model name doesn't exist there")
        return super().complete(messages, system, json_schema, max_tokens, model)


def test_a_wrong_model_name_rests_only_that_model(tmp_path):
    p = OneBadModel(["ok"] * 5, name="openrouter", is_local=False)
    router = ModelRouter({"openrouter": p}, {"plan": ["openrouter:typo-model", "openrouter:good-model"],
                                             "chat": ["openrouter:good-model"]},
                         Budget(Store(tmp_path / "m.db"), 5.0), KillSwitch())
    assert router.complete("plan", [{"role": "user", "content": "q"}], "s").text == "ok"
    assert router.complete("chat", [{"role": "user", "content": "q"}], "s").text == "ok"   # provider not paused
    tried = [c["model"] for c in p.calls]
    assert tried == ["typo-model", "good-model", "good-model"]
    router.complete("plan", [{"role": "user", "content": "q"}], "s")
    after = [c["model"] for c in p.calls]
    assert after[-1] == "good-model" and after.count("typo-model") == 1                         # the typo rests


def test_remove_also_clears_an_environment_key(tmp_home, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-setx")
    set_secret("OPENROUTER_API_KEY", "sk-or-in-app-1234")
    delete_secret("OPENROUTER_API_KEY")
    assert get_secret("OPENROUTER_API_KEY") is None


def test_key_boxes_are_not_password_boxes(app_client):
    client, _ = app_client()
    login(client)
    html = client.get("/settings").text
    for tag in re.findall(r"<input[^>]*>", html):
        if 'type="password"' in tag:
            assert 'name="password"' in tag or "current-password" in tag, tag
