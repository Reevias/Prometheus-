import io
import json

import httpx

from prometheus.mcp_server import PROTOCOL, TOOLS, stdio_bridge, token
from tests.conftest import login


def rpc(client, method, params=None, mid=1, auth=True):
    headers = {"Authorization": f"Bearer {token()}"} if auth else {}
    return client.post("/api/mcp", json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
                       headers=headers)


def call(client, name, **args):
    r = rpc(client, "tools/call", {"name": name, "arguments": args})
    return r.json()["result"]["content"][0]["text"]


def test_handshake_tools_and_auth(app_client):
    client, services = app_client()
    assert rpc(client, "initialize", auth=False).status_code == 401
    init = rpc(client, "initialize", {"protocolVersion": PROTOCOL, "clientInfo": {"name": "Antigravity"}}).json()
    assert init["result"]["serverInfo"]["name"] == "prometheus"
    names = [t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]]
    assert names == [t["name"] for t in TOOLS]
    note = client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers={"Authorization": f"Bearer {token()}"})
    assert note.status_code == 202


def test_memory_facts_and_actions_need_approval(app_client):
    client, services = app_client()
    services.store.add_memory("Prefers explanations with diagrams")
    services.orchestrator.facts.save("Ravi", "relation", "brother")
    assert "diagrams" in call(client, "search_memory", query="diagrams explanations")
    assert "relation: brother" in call(client, "facts_about", subject="Ravi")
    assert "request_action" in call(client, "ask", question="delete my Downloads folder")
    out = call(client, "request_action", task="tidy my Downloads folder", why="the user asked in Antigravity")
    assert "Approvals" in out
    big = [a for a in services.store.big_approvals() if a.kind == "mcp_action"]
    assert big and "tidy my Downloads" in big[0].summary


def test_ask_runs_like_a_typed_question_and_keeps_private_answers_in(app_client):
    client, services = app_client()
    assert call(client, "ask", question="what time is it?").startswith("It's")
    chat = [c for c, t in services.store.chats() if t == "Other apps"]
    assert chat


def test_stdio_bridge_forwards_lines(tmp_home, monkeypatch):
    token()
    seen = []

    def handler(request):
        seen.append((request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    out = io.StringIO()
    stdio_bridge(8123, stdin=io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n'), stdout=out,
                 http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen[0][0] == f"Bearer {token()}" and json.loads(out.getvalue())["id"] == 1


def test_settings_shows_setup(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/settings").text
    assert "prometheus.cli" in page and "mcpServers" in page
