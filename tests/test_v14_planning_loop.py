import json

from prometheus.agents.base import Agent, StepResult
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator


class AppOpener(Agent):
    name = "app"
    description = "opens an app (target)"
    tools = set()
    calls = []

    def run(self, goal, inputs, ctx):
        AppOpener.calls.append(inputs.get("target"))
        if inputs.get("target") == "Spotify":
            return StepResult("failed", "Spotify isn't installed.")
        return StepResult("done", f"opened {inputs.get('target')}")


class Website(Agent):
    name = "site"
    description = "opens a website (target)"
    tools = set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", f"opened {inputs['target']}")


class Liar(Agent):
    name = "liar"
    description = "says done but nothing happens"
    tools = set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", "done!", {"made": ["/nowhere/at/all.txt"]})

    def verify(self, goal, inputs, result, ctx):
        return "the file isn't there"


class Secret(Agent):
    name = "secret"
    description = "reads private files"
    tools = set()
    private_outputs = True

    def run(self, goal, inputs, ctx):
        return StepResult("failed", "salary_slip_52000.pdf could not be read")


REGISTRY = {"app": AppOpener, "site": Website, "liar": Liar, "secret": Secret}


def plan(agent, target="Spotify"):
    return json.dumps({"answer": "", "topic": "t", "title": "t", "remember": [], "steps": [
        {"id": 1, "agent": agent, "goal": "open spotify", "skill": "", "inputs": [{"key": "target", "value": target}],
         "depends_on": []}]})


def repair(agent, target, action="retry"):
    return json.dumps({"action": action, "agent": agent, "goal": "open spotify another way", "why": "x",
                       "inputs": [{"key": "target", "value": target}]})


def build(make_ctx, replies, prompts=None, cost=0.0):
    seen = prompts if prompts is not None else []

    def model(messages, system):
        seen.append(messages[-1]["content"])
        return replies.pop(0) if replies else "Done."

    fake = FakeProvider(model, name="fake", is_local=True, usd=cost)
    ctx0 = make_ctx([fake], routes={"plan": ["fake"], "combine": ["fake"], "private": ["fake"]})
    orch = Orchestrator(ctx0.store, ctx0.router, REGISTRY, lambda t, s: ctx0, ctx0.killswitch, None)
    return orch, ctx0, seen


def statuses(store, task_id):
    return [(s.agent, s.status) for s in store.steps(task_id)]


def test_failed_step_is_retried_another_way(make_ctx):
    AppOpener.calls = []
    orch, ctx0, _ = build(make_ctx, [plan("app"), repair("site", "https://open.spotify.com/"), "Opened Spotify."])
    chat = ctx0.store.create_chat("c")
    out = orch.run(chat, "get spotify going so I can listen")
    assert out.status == "done"
    assert statuses(ctx0.store, out.task_id) == [("app", "failed"), ("site", "done")]
    assert ctx0.store.steps(out.task_id)[1].goal.startswith("Try 2:")


def test_said_done_but_goal_missing_counts_as_failed(make_ctx):
    orch, ctx0, _ = build(make_ctx, [plan("liar"), repair("site", "https://x.example.com/"), "ok"])
    out = orch.run(ctx0.store.create_chat("c"), "make the file")
    assert statuses(ctx0.store, out.task_id) == [("liar", "done"), ("site", "done")]   # retried after the check


def test_stops_after_three_tries_and_never_repeats(make_ctx):
    AppOpener.calls = []
    orch, ctx0, _ = build(make_ctx, [plan("app"), repair("app", "Spotify")])       # suggests the same thing again
    out = orch.run(ctx0.store.create_chat("c"), "get spotify going so I can listen")
    assert AppOpener.calls == ["Spotify"]                                        # the repeat was refused
    orch, ctx0, _ = build(make_ctx, [plan("liar"), repair("liar", "a"), repair("liar", "b"), repair("liar", "c")])
    out = orch.run(ctx0.store.create_chat("c"), "make it")
    assert len(ctx0.store.steps(out.task_id)) == 3 and out.status == "failed"    # 3 tries, then stop


def test_give_up_stops_at_once(make_ctx):
    orch, ctx0, _ = build(make_ctx, [plan("app"), repair("", "", action="give_up"), "Spotify isn't installed."])
    out = orch.run(ctx0.store.create_chat("c"), "get spotify going so I can listen")
    assert statuses(ctx0.store, out.task_id) == [("app", "failed")]


def test_private_details_never_reach_the_retry_prompt(make_ctx):
    prompts = []
    orch, ctx0, prompts = build(make_ctx, [plan("secret"), repair("", "", action="give_up"), "x"], prompts)
    orch.run(ctx0.store.create_chat("c"), "read my salary slip")
    retry_prompt = next(p for p in prompts if "has to:" in p)
    assert "52000" not in retry_prompt and "hidden" in retry_prompt


def test_task_spending_limit_stops_retries(make_ctx):
    orch, ctx0, _ = build(make_ctx, [plan("liar"), repair("liar", "a"), repair("liar", "b"), "x"], cost=0.15)
    ctx0.router.providers["fake"].is_local = False
    orch.task_budget = 0.20
    out = orch.run(ctx0.store.create_chat("c"), "make it")
    summaries = [s.result for s in ctx0.store.steps(out.task_id)]
    assert len(summaries) < 3                                                    # stopped before the third try


def test_settings_set_the_limits(app_client):
    client, services = app_client()
    services.settings.step_tries, services.settings.task_budget_usd = 2, 0.5
    from prometheus.app import apply_settings
    apply_settings(services)
    assert services.orchestrator.step_tries == 2 and services.orchestrator.task_budget == 0.5


def test_limits_editable_in_settings(app_client):
    from tests.conftest import login

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    ok = client.post("/settings", data={"budget_usd": "5", "step_tries": "4", "task_budget_usd": "0.5"}, headers=h)
    assert ok.status_code == 200 and services.orchestrator.step_tries == 4 and services.orchestrator.task_budget == 0.5
    bad = client.post("/settings", data={"budget_usd": "5", "step_tries": "40", "task_budget_usd": "0.5"}, headers=h)
    assert bad.status_code == 400


def test_a_fix_that_worked_becomes_a_lesson_used_next_time(make_ctx):
    AppOpener.calls = []
    prompts = []
    orch, ctx0, prompts = build(make_ctx, [plan("app"), repair("site", "https://open.spotify.com/"), "ok",
                                           plan("site", "https://open.spotify.com/"), "ok"], prompts)
    orch.run(ctx0.store.create_chat("c"), "get spotify going so I can listen")
    lessons = ctx0.store.lessons()
    assert len(lessons) == 1 and lessons[0]["worked"] == 1
    assert "app[target=Spotify]" in lessons[0]["text"] and "site[target=https://open.spotify.com/] worked" in lessons[0]["text"]
    orch.run(ctx0.store.create_chat("c2"), "get spotify going so I can listen please")
    next_plan_prompt = [p for p in prompts if "Request: get spotify going so I can listen please" in p][0]
    assert "Lessons from earlier tasks" in next_plan_prompt and "open.spotify.com" in next_plan_prompt


def test_private_step_lessons_keep_file_details_out(make_ctx):
    orch, ctx0, _ = build(make_ctx, [plan("secret"), repair("secret", "other"), repair("", "", "give_up"), "x"])
    orch.run(ctx0.store.create_chat("c"), "read my salary slip")
    text = ctx0.store.lessons()[0]["text"]
    assert "52000" not in text and "none worked" in text


def test_nightly_learning_reads_task_lessons(app_client):
    import json as _json

    from prometheus.learning import current_rules, learn

    def model(messages, system):
        assert "What happened in tasks" in messages[-1]["content"]
        return _json.dumps({"rules": ["When an app isn't installed, open its website."], "summary": "apps"})

    client, services = app_client(providers=[FakeProvider(model, name="fake")])
    services.store.add_lesson("For 'open spotify': app failed, then site worked.", True)
    assert learn(services) == "apps"
    assert current_rules(services.prompts.get("learned")) == ["When an app isn't installed, open its website."]
    assert services.store.lessons_to_learn() == []
    from tests.conftest import login
    h = {"X-CSRF-Token": login(client)}
    assert "open spotify" in client.get("/memory?view=classic").text
    client.post(f"/memory/task-lessons/{services.store.lessons()[0]['id']}/delete", headers=h)
    assert services.store.lessons() == []
