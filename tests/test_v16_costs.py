import time

from prometheus.memory.store import Store
from prometheus.safety.budget import Budget, for_chat, month_start
from tests.conftest import login


def test_monthly_limit_warns_once_at_80_percent(tmp_path):
    store = Store(tmp_path / "m.db")
    budget = Budget(store, 1.00)
    warned = []
    budget.on_warn = warned.append
    store._write("INSERT INTO costs (provider, model, usd, tokens_in, tokens_out, created) VALUES "
                 "('anthropic', 'old', 5.0, 1, 1, ?)", (month_start() - 86400,))       # last month doesn't count
    for_chat(7)
    budget.record("anthropic", "claude", 0.50, 100, 50)
    assert not warned and budget.state().spent == 0.5
    budget.record("anthropic", "claude", 0.35, 100, 50)
    budget.record("anthropic", "claude", 0.01, 100, 50)
    assert len(warned) == 1 and "80%" in warned[0]
    assert store.cost_report(month_start())["by_chat"][0]["chat_id"] == 7


def test_costs_page(app_client):
    client, services = app_client()
    login(client)
    chat = services.store.create_chat("Physics notes")
    services.store.add_cost("anthropic", "claude-sonnet", 0.0123, 1200, 300, chat_id=chat)
    services.store.add_cost("openrouter", "deepseek", 0.002, 500, 100)
    page = client.get("/costs")
    assert page.status_code == 200
    for text in ("claude-sonnet", "deepseek", "Physics notes", "Background jobs", "$0.0123", "1,200"):
        assert text in page.text
