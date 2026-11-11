"""Features on and off, Stop all that stays stopped, practice tests, the weekly report card, the ready-made jobs,
spoken numbers, and the shortcut's app id."""
import json
import time
from types import SimpleNamespace

from prometheus import features, practice
from prometheus.config import Settings
from tests.conftest import login


def test_switches_use_old_settings_or_the_off_list():
    s = Settings()
    assert features.on(s, "clipboard") and features.on(s, "screen")
    features.set_on(s, "clipboard", False)
    features.set_on(s, "screen", False)
    assert s.clip_on is False and s.features_off == ["screen"]
    assert "switched off" in features.agent_blocked(s, "screen") and features.agent_blocked(s, "files") == ""
    features.set_on(s, "screen", True)
    assert s.features_off == [] and features.agent_blocked(s, "screen") == ""
    s.paused = True
    assert "stopped" in features.agent_blocked(s, "email").lower() and not features.running(s, "sync")
    assert {f.key for f in features.FEATURES} >= {"weekly_report", "jobs", "instant", "phone", "sync", "webhooks"}


def test_planner_never_offers_a_switched_off_agent(app_client):
    client, services = app_client()
    names = [n for n, _ in services.orchestrator._usable_agents()]
    assert "screen" in names
    features.set_on(services.settings, "screen", False)
    assert "screen" not in [n for n, _ in services.orchestrator._usable_agents()]


def test_features_page_and_stop_all_stays_stopped(app_client):
    client, services = app_client()
    csrf = login(client)
    page = client.get("/settings").text
    assert "Features on and off" in page and "Show advanced settings" in page and 'class="set adv"' in page
    r = client.post("/settings/feature", data={"key": "screen", "on": ""}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and "screen" in services.settings.features_off
    client.post("/kill", headers={"X-CSRF-Token": csrf})
    assert services.settings.paused and services.killswitch.is_set
    from prometheus.config import load_settings

    assert load_settings().paused                     # saved: a restart stays stopped
    client.post("/kill/reset", headers={"X-CSRF-Token": csrf})
    assert not services.settings.paused and not services.killswitch.is_set


def test_background_jobs_rest_while_stopped(app_client):
    client, services = app_client()
    services.settings.paused = True
    assert services.briefing.tick() == []


def test_spoken_numbers_reach_the_instant_timer():
    from prometheus.quick import clean, understand

    assert clean("set a timer for ten minutes") == "set a timer for 10 minutes"
    assert understand("set a timer for ten minutes").steps[0]["inputs"]["when"] == "in 10 minutes"
    assert clean("open one drive") == "open one drive"                  # only amounts of time change
    assert understand("what assignments are due this week").steps[0]["agent"] == "assignments"


class FakeOrch:
    def __init__(self, store, plan_agent):
        self.store, self.plan_agent = store, plan_agent

    def _plan(self, text):
        return SimpleNamespace(steps=[{"agent": self.plan_agent}] if self.plan_agent else [])


class KV:
    def __init__(self):
        self.kv = {}

    def kv_get(self, k, d=None):
        return self.kv.get(k, d)

    def kv_set(self, k, v):
        self.kv[k] = v


def test_practice_tests_pass_and_fail_without_doing_anything():
    store = KV()
    assert len(practice.tests(store)) >= 10                   # a starting set
    practice.save(store, [{"said": "what time is it", "expect": "instant"},
                          {"said": "what's the weather like in Delhi", "expect": "research"},
                          {"said": "email my professor", "expect": "email"}])
    result = practice.run(FakeOrch(store, "github"))          # a broken update: everything goes to GitHub
    assert result["passed"] == 1 and result["total"] == 3
    assert "went to github, not email" in practice.describe(result)
    assert practice.last_result(store).startswith("1 of 3 passed")


def test_save_this_as_a_practice_test(app_client):
    client, services = app_client()
    store = services.store
    chat = store.create_chat("t")
    tid = store._write("INSERT INTO tasks (chat_id, text, status, created) VALUES (?, ?, 'done', ?)",
                       (chat, "summarise my physics notes", time.time()))
    store._write("INSERT INTO steps (task_id, agent, goal, inputs, status, created) VALUES (?, 'documents', 'x', "
                 "'{}', 'done', ?)", (tid, time.time()))
    assert "should go to documents" in practice.from_last_task(store, chat)
    assert {"said": "summarise my physics notes", "expect": "documents"} in practice.tests(store)


def test_weekly_report_card_counts_the_week(app_client):
    client, services = app_client()
    store, now = services.store, time.time()
    chat = store.create_chat("t")
    for status in ("done", "done", "done", "failed"):
        store._write("INSERT INTO tasks (chat_id, text, status, created) VALUES (?, ?, ?, ?)",
                     (chat, f"job {status}", status, now - 3600))
    store._write("INSERT INTO tasks (chat_id, text, status, created) VALUES (?, 'old', 'failed', ?)",
                 (chat, now - 10 * 86400))
    from prometheus.report import week

    text = week(services, now)
    assert "4 asked, 3 done (75%), 1 didn't work out" in text and "job failed" in text and "old" not in text
    assert "Fewer than 8 in 10" in text


def test_ready_made_jobs(make_ctx, tmp_home):
    from prometheus.agents.jobs_agent import JobsAgent
    from prometheus.models.fake import FakeProvider

    actions = json.dumps({"decisions": ["Lab moved to Friday"],
                          "actions": [{"who": "", "what": "Send the lab report", "when": "Thursday"}],
                          "follow_up": "Hi, a quick note after today's class."})
    ctx = make_ctx([FakeProvider([actions], name="fake")])
    res = JobsAgent().run("notes", {"action": "actions", "text": "We agreed the lab moves to Friday. Report Thursday."},
                          ctx)
    assert res.status == "done" and "Send the lab report — TBC, Thursday" in res.summary and "not sent" in res.summary

    news = [{"title": "AI wins", "url": "https://a.example/1", "source": "A", "date": "2026-11-10", "body": "x"},
            {"title": "No link", "url": "", "source": "B", "date": "", "body": "y"}]
    ctx = make_ctx([FakeProvider([json.dumps({"items": [{"n": 1, "why": "matters"}, {"n": 7, "why": "made up"}]})],
                                 name="fake")], routes={"research": ["fake"]})
    res = JobsAgent(search=lambda q, n: news).run("digest", {"action": "digest", "topics": "AI"}, ctx)
    assert "https://a.example/1" in res.summary and "made up" not in res.summary and "No link" not in res.summary


def test_pdfs_to_a_sheet_marks_missing_and_check(make_ctx, tmp_home, monkeypatch):
    from prometheus.agents import jobs_agent
    from prometheus.models.fake import FakeProvider

    folder = tmp_home.home / "Bills"
    folder.mkdir()
    for n in ("a.pdf", "b.pdf"):
        (folder / n).write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(jobs_agent, "extract_text", lambda p, limit=0: f"Bill {p.name} total 500")
    replies = [json.dumps({"f0": {"value": "500", "sure": True}, "f1": {"value": "", "sure": True}}),
               json.dumps({"f0": {"value": "450", "sure": False}, "f1": {"value": "12 Nov", "sure": True}})]
    ctx = make_ctx([FakeProvider(replies, name="fake")])
    res = jobs_agent.JobsAgent().run("sheet", {"action": "sheet", "path": str(folder), "fields": "amount, date",
                                               "name": "Bills"}, ctx)
    assert res.status == "done", res.summary
    assert "1 cells MISSING, 1 marked CHECK" in res.summary
    assert (tmp_home.home / "Documents" / "Prometheus sheets" / "Bills.xlsx").exists()


def test_shortcut_gets_the_app_id_and_a_stable_icon(monkeypatch, tmp_path):
    import subprocess

    from prometheus import launcher

    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    seen = []
    launcher.make_shortcuts(lambda argv, env=None, **kw: (seen.append(env), subprocess.CompletedProcess(argv, 0))[1],
                            windows=True)
    assert all(e["PROM_APPID"] == launcher.APP_ID for e in seen)
    assert all(e["PROM_ICON"] == str(tmp_path / "Local" / "Prometheus" / "prometheus.ico") for e in seen)
    assert (tmp_path / "Local" / "Prometheus" / "prometheus.ico").exists()
    assert "System.AppUserModel" in launcher.SHORTCUT_PS or "9F4C2855" in launcher.SHORTCUT_PS


def test_page_scripts_survive_being_run_twice_and_errors_never_replace_the_page(app_client):
    import re

    client, _ = app_client()
    login(client)
    page = client.get("/settings").text
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    # buttons that reload the whole page run these scripts again: a top-level const/let would crash the second time
    assert not any(re.search(r"^  (?:const|let) ", s, re.M) for s in scripts)
    assert "promErrorsReady" in page and "htmx:beforeSwap" in page and "shouldSwap = false" in page


def test_saving_the_elevenlabs_key_answers_next_to_the_box(app_client):
    client, services = app_client()
    csrf = login(client)
    bad = client.post("/settings/keys", data={"name": "ELEVENLABS_API_KEY", "value": "sk_abcdef123456",
                                              "password": "nope"}, headers={"X-CSRF-Token": csrf})
    voice = bad.text.split('id="voice"', 1)[-1].split('id="connections"', 1)[0]
    assert "Wrong password" in voice or "password" in voice.lower()
