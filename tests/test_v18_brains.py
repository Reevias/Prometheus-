from types import SimpleNamespace

from prometheus.config import Settings
from prometheus.models.router import RESEARCH_BRAINS, build_routes, openrouter_models
from tests.conftest import login


def test_research_brain_is_an_option_not_the_default():
    assert Settings().research_brain == "auto"
    auto = build_routes(Settings())["research"]
    assert "openrouter:moonshotai/kimi-k3" not in auto
    kimi = build_routes(Settings(research_brain="moonshotai/kimi-k3"))["research"]
    assert kimi[0] == "openrouter:moonshotai/kimi-k3" and len(kimi) > 1                 # the old route stays behind


def test_model_list_and_settings(app_client):
    models = openrouter_models()
    assert len(models) >= 15 and all("/" in m["id"] for m in models)
    assert "qwen/qwen3.8-2.4t-a95b" in RESEARCH_BRAINS
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    assert "Qwen 3.8 (2.4T)" in client.get("/settings").text
    client.post("/settings/research-brain", data={"brain": "deepseek/deepseek-v4-pro"}, headers=h)
    assert services.settings.research_brain == "deepseek/deepseek-v4-pro"
    assert services.router.routes["research"][0] == "openrouter:deepseek/deepseek-v4-pro"
    assert client.post("/settings/research-brain", data={"brain": "nope"}, headers=h).status_code == 400
