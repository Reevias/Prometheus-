"""M3.2: every tool says what it needs and where it runs; jobs for an absent device wait for it."""
from __future__ import annotations

from prometheus import phone_wait
from prometheus.agents import REGISTRY
from prometheus.connectors.android import PhoneError
from prometheus.orchestrator.registry import needs_laptop, planner_line, runs_on, specs


def test_every_agent_has_a_spec_with_where_it_runs():
    got = {s.name: s for s in specs(REGISTRY)}
    assert set(got) == set(REGISTRY) and len(got) > 25
    assert got["files"].runs_on == "laptop" and got["screen"].runs_on == "laptop"
    assert got["phone"].runs_on == "phone"
    assert got["research"].runs_on == "both" and got["reminders"].runs_on == "both"
    assert "system_change" in got["phone"].scope
    assert got["opener"].required_inputs                      # what it must always be given


def test_the_planner_is_told_where_each_tool_runs():
    assert "[" in planner_line("files", REGISTRY["files"]) and "laptop only" in planner_line("files", REGISTRY["files"])
    assert "needs the phone" in planner_line("phone", REGISTRY["phone"])
    assert "laptop only" not in planner_line("research", REGISTRY["research"])
    assert needs_laptop([{"agent": "research"}, {"agent": "files"}]) and not needs_laptop([{"agent": "research"}])
    assert runs_on("unknown-agent") == "both"


class FakePhone:
    def __init__(self, back):
        self.target, self.back, self.connects = "100.64.0.2:5555", back, 0

    def shell(self, *a, **k):
        if not self.back:
            raise PhoneError("The phone isn't connected right now.")
        return "ok"

    def connect(self, target):
        self.connects += 1
        if not self.back:
            raise PhoneError("Couldn't connect")
        return "Connected"


def test_a_phone_job_waits_for_the_phone_and_runs_when_it_is_back(app_client):
    client, services = app_client()
    st = services.store
    chat = st.create_chat("x")
    started, said = [], []
    services.start_task = lambda c, t, read_only=False: started.append((c, t))
    services.notify = lambda kind, text: said.append(text)
    assert phone_wait.queue(st, chat, "set an alarm on my phone for 6")
    assert not phone_wait.queue(st, chat, "set an alarm on my phone for 6")          # once only
    phone = FakePhone(back=False)
    w = phone_wait.PhoneWaiter(services, make_phone=lambda: phone)
    assert w.once() == 0 and started == [] and len(phone_wait.jobs(st)) == 1         # still away: keeps waiting
    phone.back = True
    assert w.once() == 1 and started == [(chat, "set an alarm on my phone for 6")]
    assert phone_wait.jobs(st) == [] and said and "phone is back" in said[-1]
    assert any("phone is back" in m.text for m in st.messages(chat))


def test_a_request_waits_at_most_a_day(app_client):
    client, services = app_client()
    st = services.store
    chat = st.create_chat("x")
    phone_wait.queue(st, chat, "call mum", now=0)
    w = phone_wait.PhoneWaiter(services, make_phone=lambda: FakePhone(back=True))
    services.start_task = lambda *a, **k: None
    assert w.once(now=phone_wait.MAX_WAIT + 10) == 0 and phone_wait.jobs(st) == []
    assert any("dropped" in m.text for m in st.messages(chat))


def test_the_phone_agent_queues_when_the_phone_is_away(make_ctx, monkeypatch):
    import prometheus.agents.phone_agent as pa

    class Away:
        def battery(self):
            raise PhoneError("The phone isn't connected right now. Open Settings → Phone control → Connect")

    class S:
        notify = staticmethod(lambda *a, **k: None)
        settings = None
    monkeypatch.setattr(pa, "phone", lambda: Away())
    monkeypatch.setattr(pa, "SERVICES", S)
    ctx = make_ctx()
    res = pa.PhoneAgent().run("battery", {"action": "battery"}, ctx)
    assert res.status == "waiting_approval" and "waits" in res.summary
    assert len(phone_wait.jobs(ctx.store)) == 1
