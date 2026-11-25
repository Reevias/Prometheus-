"""M5 audit (25 Nov): the four risks found by the laptop check, plus the audit's own findings. Each failed first."""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import psutil
import pytest
from conftest import login

from prometheus.actions.terminal import AsyncTerminal
from prometheus.jobs import graph as g
from prometheus.jobs import limits
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import JobStore
from prometheus.safety.killswitch import KillSwitch
from prometheus.safety.paths import PathPolicy

GB = 1024 ** 3


def _until(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs.db")


# -- risk 1: under 1 GB free (normal on the 8 GB laptop) work goes one at a time, never stops for ever ----------------
def test_low_memory_means_one_at_a_time_not_none():
    slots = limits.RamSlots(free_bytes=lambda: 0.3 * GB)
    assert slots.try_acquire() is True
    assert slots.try_acquire() is False
    assert "memory" in slots.reason.lower() and "0.3 GB free" in slots.reason
    slots.release()
    assert slots.try_acquire() is True


def test_a_graph_finishes_on_a_laptop_with_0_3_gb_free(store):
    graphs = g.GraphRunner(store, JobRunner(store), slots=limits.RamSlots(free_bytes=lambda: 0.3 * GB))
    now, most, lock = [0], [0], threading.Lock()

    def work(ctx, inputs):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        time.sleep(0.03)
        with lock:
            now[0] -= 1

    gid = graphs.create("low ram", [{"id": f"s{i}", "func": work} for i in range(3)])
    graphs.start(gid)
    assert graphs.wait(gid, 10) == DONE and most[0] == 1


def test_tests_never_depend_on_the_machines_free_memory():
    # the laptop check hung because tests read the real RAM; conftest gives every test a fixed, roomy reading
    assert limits.child_slots().try_acquire() and limits.child_slots().free_gb == 4.0
    limits.child_slots().release()


# -- risk 2: Stop all ends everything, starts nothing new, and never calls a stopped step "done" ---------------------
TREE = ("import subprocess, sys, time, pathlib\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(f'{c.pid} ' + str(__import__('os').getpid()))\n"
        "time.sleep(60)\n")


def test_stop_all_leaves_nothing_running_and_starts_nothing_new(store, tmp_path):
    home = tmp_path / "home"
    for d in ("data", "code", "project"):
        (home / d).mkdir(parents=True)
    term = AsyncTerminal(policy=PathPolicy(home=home, data_dir=home / "data", code_dir=home / "code"))
    ks = KillSwitch()
    limits.connect_stop_all(ks, store)                 # the same wiring the app uses
    graphs = g.GraphRunner(store, JobRunner(store), slots=limits.RamSlots(free_bytes=lambda: 2 * GB), killswitch=ks)
    started = []

    def step(i):
        def run(ctx, inputs):
            started.append(i)
            asyncio.run(term.execute([sys.executable, "-c", TREE, str(tmp_path / f"pids{i}")], cwd=home / "project",
                                     timeout=60))
        return run

    gid = graphs.create("three", [{"id": f"c{i}", "func": step(i)} for i in (1, 2, 3)])
    graphs.start(gid)
    pidfiles = [tmp_path / "pids1", tmp_path / "pids2"]
    assert _until(lambda: all(p.exists() and len(p.read_text().split()) == 2 for p in pidfiles))
    ks.trigger("test")
    assert _until(lambda: graphs.idle(gid))
    time.sleep(0.5)                                     # time for a wrongly started third step to show itself
    assert started == [1, 2] or sorted(started) == [1, 2]
    pids = [int(x) for p in pidfiles for x in p.read_text().split()]
    assert _until(lambda: all(gone(p) for p in pids)), pids
    assert not (tmp_path / "pids3").exists() and limits.running_programs() == {}
    states = {j.payload["step"]: j.state for j in store.list_jobs(parent_id=gid)}
    assert states == {"c1": PAUSED, "c2": PAUSED, "c3": PENDING}, states      # never "done"
    assert store.get_job(gid).state == PAUSED


def test_a_step_that_returns_early_is_not_marked_done_by_the_runner(store):
    job = store.create_job("graph_step", "x", state=PENDING)
    store.set_state(job.id, RUNNING)
    t = JobRunner(store).run_existing(job.id, lambda: None, mark_running=False)
    t.join(5)
    assert store.get_job(job.id).state == RUNNING      # the caller records how it ended, not the runner


def test_a_program_started_while_everything_is_stopped_is_ended_at_once(store, tmp_path):
    ks = KillSwitch()
    limits.connect_stop_all(ks, store)
    ks.trigger("test")
    job = store.create_job("graph_step", "late", state=RUNNING)
    from prometheus.jobs import runner as runner_mod
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         **({"start_new_session": True} if sys.platform != "win32" else {}))
    try:
        def inside():
            runner_mod._CURRENT_JOB_ID.set(job.id)
            runner_mod._CURRENT_JOB_RUNNER.set(JobRunner(store))
            return limits.track(p.pid, p.pid if sys.platform != "win32" else None, None)

        import contextvars
        token = contextvars.copy_context().run(inside)
        assert _until(lambda: p.poll() is not None)
        limits.untrack(token)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    ks.reset()


# -- risk 3: a chat task stopped or cut off by a restart gets "Run again" ------------------------------------------
def test_a_task_cut_off_by_a_restart_can_be_run_again_from_the_jobs_view(app_client):
    client, services = app_client()
    csrf = login(client)
    chat = services.store.create_chat("c")
    job = services.jobs.create_job("task", f"Task in chat {chat}: hello", payload={"chat_id": chat, "text": "hello"},
                                   state=RUNNING)
    services.jobs.recover_after_restart()
    from prometheus.web import jobs_view

    row = next(r for r in jobs_view.build(services.jobs, None, services.graphs)["jobs"] if r["id"] == job.id)
    assert row["state"] == PAUSED and "rerun" in row["controls"]
    ran = []
    services.start_task = lambda chat_id, text, read_only=False: ran.append((chat_id, text))
    r = client.post(f"/api/v3/jobs/{job.id}/rerun", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and ran == [(chat, "hello")]
    assert services.jobs.get_job(job.id).state == CANCELLED and "again" in services.jobs.get_job(job.id).error


def test_run_again_is_refused_while_everything_is_stopped(app_client):
    client, services = app_client()
    csrf = login(client)
    chat = services.store.create_chat("c")
    job = services.jobs.create_job("task", "t", payload={"chat_id": chat, "text": "hello"}, state=PAUSED)
    services.killswitch.trigger("test")
    ran = []
    services.start_task = lambda chat_id, text, read_only=False: ran.append(1)
    r = client.post(f"/api/v3/jobs/{job.id}/rerun", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409 and ran == []
    services.killswitch.reset()


def test_new_task_jobs_remember_their_chat_and_request(app_client):
    _, services = app_client()
    chat = services.store.create_chat("c")
    release = threading.Event()
    services.orchestrator.run = lambda chat_id, text, read_only=False: release.wait(5) and None
    services.start_task(chat, "hello there")
    [job] = services.jobs.list_jobs(kind="task")
    assert job.payload["chat_id"] == chat and job.payload["text"] == "hello there"
    release.set()


# -- risk 4: chats sent at the same time share a limit too ---------------------------------------------------------
def test_five_chats_at_once_run_at_most_two_at_a_time(app_client, monkeypatch):
    _, services = app_client()
    monkeypatch.setattr(limits, "_TASK_SLOTS", limits.RamSlots(free_bytes=lambda: 2 * GB))
    now, most, lock, release = [0], [0], threading.Lock(), threading.Event()

    def run(chat_id, text, read_only=False):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        release.wait(5)
        with lock:
            now[0] -= 1

    services.orchestrator.run = run
    services.after_task = lambda *a, **k: None
    chats = [services.store.create_chat(f"c{i}") for i in range(5)]
    for c in chats:
        services.start_task(c, f"job {c}")
    assert _until(lambda: now[0] == 2)
    time.sleep(0.3)
    assert now[0] == 2
    waiting = [j for j in services.jobs.list_jobs(kind="task") if j.progress == "Waiting for a free slot"]
    assert len(waiting) == 3
    release.set()
    assert _until(lambda: all(j.state == DONE for j in services.jobs.list_jobs(kind="task")))
    assert most[0] == 2


def test_chat_tasks_and_their_own_steps_use_separate_limits():
    # a task holding a slot must never wait for a slot its own graph steps need (that would hang for ever)
    assert limits.task_slots() is not limits.child_slots()


# -- audit A1: a finished graph's functions and context are let go (memory grows on the 8 GB laptop otherwise) ------
def test_a_finished_or_cancelled_graph_is_forgotten_from_memory(store):
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    done = graphs.create("done", [{"id": "a", "func": lambda ctx, inputs: "ok"}])
    graphs.start(done)
    assert graphs.wait(done, 5) == DONE and _until(lambda: graphs.idle(done))
    stopped = graphs.create("stopped", [{"id": "a", "func": lambda ctx, inputs: ctx.wait(10)}])
    graphs.start(stopped)
    assert _until(lambda: store.list_jobs(parent_id=stopped)[0].state == RUNNING)
    graphs.cancel(stopped)
    assert _until(lambda: graphs.idle(stopped))
    paused = graphs.create("paused", [{"id": "a", "func": lambda ctx, inputs: ctx.wait(10)}])
    graphs.start(paused)
    assert _until(lambda: store.list_jobs(parent_id=paused)[0].state == RUNNING)
    graphs.pause(paused)
    assert _until(lambda: not any(k[0] in (done, stopped) for k in graphs._funcs))
    assert done not in graphs._contexts and stopped not in graphs._contexts
    assert any(k[0] == paused for k in graphs._funcs)          # a paused one keeps them, so Resume still works
    graphs.cancel(paused)


# -- review F1: a chat task's graph can't be paused into a false "done" from the jobs panel -------------------------
def test_a_task_graph_has_only_stop_in_the_panel():
    from types import SimpleNamespace

    from prometheus.web import jobs_view

    for state in (RUNNING, PAUSED, PENDING):
        job = SimpleNamespace(state=state, parent_id=None, kind="task_graph", payload={"graph": True, "chat_id": 1})
        assert jobs_view.controls_for(job, None, None) == ["stop"]


def test_a_task_graph_paused_from_outside_never_ends_the_task_as_done(make_ctx):
    import dataclasses
    import json

    from prometheus.agents.base import Agent, StepResult
    from prometheus.config import Settings
    from prometheus.models.fake import FakeProvider
    from prometheus.orchestrator.planner import Orchestrator

    calls = []
    holder = {}

    class Pauser(Agent):
        name, description, tools = "pauser", "pauses its own graph", set()

        def run(self, goal, inputs, ctx):
            calls.append(goal)
            [gj] = holder["jobs"].list_jobs(kind="task_graph")
            holder["graphs"].pause(gj.id)
            return StepResult("done", "ok")

    steps = [{"id": i, "agent": "pauser", "goal": f"g{i}", "inputs": [], "depends_on": []} for i in (1, 2)]
    ctx0 = make_ctx(providers=[FakeProvider([json.dumps({"steps": steps})], name="planner"),
                               FakeProvider(["All done."], name="combiner")],
                    routes={"plan": ["planner"], "combine": ["combiner"], "private": ["planner"]})
    orch = Orchestrator(ctx0.store, ctx0.router, {"pauser": Pauser},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch, None)
    orch.settings = Settings(parallel_steps=True)
    jobs = JobStore(ctx0.store)
    holder.update(jobs=jobs, graphs=g.GraphRunner(jobs, JobRunner(jobs), max_parallel=1))
    orch.graphs = holder["graphs"]
    out = orch.run(ctx0.store.create_chat("c"), "x")
    assert out.status != "done", out
    [gj] = jobs.list_jobs(kind="task_graph")
    assert gj.state == CANCELLED                                 # nothing left that a Resume could run twice


# -- review F2: Stop all never leaves a finished job looking paused for ever ----------------------------------------
def test_a_job_that_finishes_after_stop_all_records_how_it_really_ended(store):
    from prometheus.web import jobs_view

    runner = JobRunner(store)
    go = threading.Event()
    plain = runner.start_job("backup", "Backup now", lambda: go.wait(5))
    task = runner.start_job("task", "Task in chat 1: hi", lambda: go.wait(5), payload={"chat_id": 1, "text": "hi"})
    assert _until(lambda: store.get_job(plain.id).state == RUNNING and store.get_job(task.id).state == RUNNING)
    store.pause_all("Stopped by user: test")
    go.set()
    assert _until(lambda: store.get_job(plain.id).state == CANCELLED and store.get_job(task.id).state == CANCELLED)
    assert "Stopped by user" in store.get_job(plain.id).error
    assert jobs_view.controls_for(store.get_job(task.id), runner) == ["rerun"]     # a stopped chat task: Run again
    assert jobs_view.controls_for(store.get_job(plain.id), runner) == []


def test_a_job_paused_by_hand_that_finishes_is_done(store):
    runner = JobRunner(store)
    go = threading.Event()
    job = runner.start_job("x", "x", lambda: go.wait(5))
    assert _until(lambda: store.get_job(job.id).state == RUNNING)
    runner.pause_job(job.id)
    go.set()
    assert _until(lambda: store.get_job(job.id).state == DONE)


# -- review F3: branching a chat that is left out of memory keeps the branch out too --------------------------------
def test_a_branch_of_a_left_out_chat_is_left_out_too(app_client):
    client, services = app_client()
    csrf = login(client)
    chat = services.store.create_chat("secret")
    services.store.add_message(chat, "user", "my bank pin is 4321")
    mid = services.store.messages(chat)[-1].id
    services.settings.memory_off_chats = [chat]
    r = client.post(f"/api/v3/chats/{chat}/messages/{mid}/branch", headers={"X-CSRF-Token": csrf})
    new = r.json()["chat_id"]
    from prometheus.config import load_settings

    assert new in services.settings.memory_off_chats and new in load_settings().memory_off_chats
    r = client.post(f"/chats/{new}/branch/{services.store.messages(new)[-1].id}", headers={"X-CSRF-Token": csrf})
    newer = int(r.headers["HX-Redirect"].rsplit("/", 1)[1])
    assert newer in services.settings.memory_off_chats              # a branch of the branch too


# -- review F4: the code sandbox a child agent runs is ended by cancel / Stop all -----------------------------------
def test_cancelling_a_step_ends_its_sandbox_program(store, tmp_path):
    from prometheus import sandbox

    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    out = {}

    def code_step(ctx, inputs):
        t = time.monotonic()
        out["res"] = sandbox.run_locked("import time\nprint('started', flush=True)\ntime.sleep(30)\n",
                                        sandbox.new_folder(tmp_path), timeout=60)
        out["took"] = time.monotonic() - t

    gid = graphs.create("code", [{"id": "a", "func": code_step}])
    graphs.start(gid)
    assert _until(lambda: limits.running_programs() != {})
    graphs.cancel(gid)
    assert _until(lambda: "took" in out, 15) and out["took"] < 10
    assert limits.running_programs() == {}


# -- review F5: a retry's clean-up never ends the next try's programs -----------------------------------------------
def test_ending_one_try_leaves_the_next_try_alone(store):
    import subprocess

    from prometheus.jobs import runner as runner_mod

    job = store.create_job("graph_step", "s", state=RUNNING)
    procs, tokens = [], []
    try:
        for attempt in (1, 2):
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 **({"start_new_session": True} if sys.platform != "win32" else {}))
            procs.append(p)

            def inside(p=p, attempt=attempt):
                runner_mod._CURRENT_JOB_ID.set(job.id)
                runner_mod._CURRENT_JOB_RUNNER.set(JobRunner(store))
                limits.CURRENT_ATTEMPT.set(attempt)
                return limits.track(p.pid, p.pid if sys.platform != "win32" else None, None)

            import contextvars
            tokens.append(contextvars.copy_context().run(inside))
        limits.end_job(job.id, attempt=1)
        assert _until(lambda: procs[0].poll() is not None)
        time.sleep(0.3)
        assert procs[1].poll() is None
    finally:
        for t in tokens:
            limits.untrack(t)
        for p in procs:
            if p.poll() is None:
                p.kill()
            p.wait()


# -- review F6: the job table is tidied and the panel never reads all of it ----------------------------------------
def test_old_finished_jobs_are_tidied_away(store):
    old = time.time() - 40 * 86400
    gone_job = store.create_job("front_door", "x", state=DONE, created=old)
    store.update_job(gone_job.id, finished=old)
    graph = store.create_job("graph", "old graph", state=DONE, created=old, payload={"graph": True})
    store.update_job(graph.id, finished=old)
    step = store.create_job("graph_step", "s", parent_id=graph.id, state=DONE, created=old)
    store.update_job(step.id, finished=old)
    keep_new = store.create_job("front_door", "y", state=DONE)
    keep_paused = store.create_job("task", "t", state=PAUSED, created=old)
    assert store.prune_finished(days=30) == 3
    left = {j.id for j in store.list_jobs()}
    assert left == {keep_new.id, keep_paused.id}


def test_the_panel_never_reads_the_whole_job_table(store, monkeypatch):
    from prometheus.web import jobs_view

    for i in range(30):
        store.create_job("front_door", f"x{i}", state=DONE)
    store.create_job("task", "Task in chat 1: hi", state=PAUSED, payload={"chat_id": 1, "text": "hi"})
    real = store.list_jobs

    def guarded(state=None, kind=None, parent_id=None):
        assert not (state is None and kind is None and parent_id is None), "read every job"
        return real(state=state, kind=kind, parent_id=parent_id)

    monkeypatch.setattr(store, "list_jobs", guarded)
    view = jobs_view.build(store, None)
    assert [r["title"] for r in view["jobs"]] == ["Task in chat 1: hi"]


# -- review F7: a waiting request that ran again records its real result ---------------------------------------------
def test_a_retried_request_that_failed_is_not_recorded_as_done(tmp_path):
    from prometheus import resume
    from prometheus.jobs.states import FAILED
    from prometheus.memory.store import Store

    s = Store(tmp_path / "memory.db")
    resume.note_limit(s, chat_id=7, text="hello", error="rate limit exceeded", now=1000.0)
    js = JobStore(s)
    job = js.list_jobs(kind="resume")[0]
    resume._without(s, job.payload["id"], status="failed")
    assert js.get_job(job.id).state == FAILED
