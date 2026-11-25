"""The multi-agent view (M5 5.5): every job and task graph, read from the job tracker and nothing else.

What is shown: title, agent, state, the real start time, run time, progress text and (only when a program of the
job really exists) its memory. A button appears only when the move is allowed and really does something:
- a graph (running: Pause, Stop; paused: Resume, Stop; waiting: Stop)
- a plain job that is paused or waiting: Stop (nothing runs, so closing it is true); a running one only when it is an
  asyncio task (a running Python thread can't be stopped from outside, so no button pretends it can)
- the steps of a graph follow their graph and have no buttons of their own
Always-on loops (reminders, sync, ...) are not listed, and finished jobs are listed only when they ended badly or
are graphs (the rest would be noise: one finished job per chat message).
"""
from __future__ import annotations

import re
import time

from prometheus.jobs import limits
from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING, TERMINAL_STATES

ACTIONS = ("pause", "resume", "stop", "rerun")
RERUN_KINDS = {"task"}            # chat tasks: "Run again" starts the request again (as on the Tasks page)
FINISHED_SHOWN = 8
AGENT_PREFIX = re.compile(r"^([a-z_]{2,30}): ")
DIAGRAM_STATE = {PENDING: "waiting", PAUSED: "waiting", RUNNING: "running", DONE: "done", FAILED: "failed",
                 CANCELLED: "cancelled"}


def is_graph(job) -> bool:
    return bool(job.payload.get("graph"))


def duration_text(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s} s"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def memory_text(nbytes) -> str:
    if not nbytes:
        return ""
    mb = nbytes / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def controls_for(job, runner, graphs=None) -> list[str]:
    """The buttons that are allowed for this job right now (in the order they are shown)."""
    if job.parent_id is not None:
        return []
    if job.state in TERMINAL_STATES:
        # a chat task Stop all ended: its request can run again (M5 audit F2)
        if job.state == CANCELLED and job.kind in RERUN_KINDS and (job.error or "").startswith("Stopped by user") \
                and job.payload.get("chat_id") is not None and job.payload.get("text"):
            return ["rerun"]
        return []
    if is_graph(job) and job.kind == "task_graph":
        return ["stop"]                   # a chat task's graph: Stop ends the task (pause would fake a "done")
    if is_graph(job):
        if job.state == RUNNING:
            return ["pause", "stop"]
        if job.state == PAUSED:
            can = job.payload.get("resumable", True) or (graphs is not None and graphs.can_resume(job.id))
            return (["resume"] if can else []) + ["stop"]
        return ["stop"]
    if job.state == PAUSED and job.kind in RERUN_KINDS and job.payload.get("chat_id") is not None \
            and job.payload.get("text"):
        return ["rerun", "stop"]          # a stopped or restarted chat task: its request can run again
    if job.state in (PENDING, PAUSED):
        return ["stop"]
    if job.state == RUNNING and runner is not None and runner.get_task(job.id) is not None:
        return ["stop"]
    return []


def _agent_of(job, agents) -> str:
    named = job.payload.get("agent")
    if named:
        return str(named)
    m = AGENT_PREFIX.match(job.title or "")
    if m and (agents is None or m.group(1) in agents):
        return m.group(1)
    return ""


def _run_seconds(job, now: float) -> float:
    if not job.started:
        return 0.0
    return max(0.0, (job.finished or now) - job.started)


def _memory(store, job_id: int):
    try:
        return limits.child_status(store, job_id).get("memory_bytes")
    except Exception:                                  # noqa: BLE001 - a reading that fails is shown as nothing
        return None


def _levels(steps: list) -> dict[str, int]:
    needs = {s.payload.get("step", str(s.id)): list(s.payload.get("needs") or []) for s in steps}
    level: dict[str, int] = {}

    def depth(k: str, seen: tuple = ()) -> int:
        if k in level:
            return level[k]
        if k in seen or k not in needs:
            return 0
        level[k] = 1 + max((depth(n, seen + (k,)) for n in needs[k]), default=-1)
        return level[k]

    for k in needs:
        depth(k)
    return level


def _step_rows(store, steps: list, agents) -> tuple[list[dict], dict]:
    level = _levels(steps)
    rows, nodes, edges = [], [], []
    for s in steps:
        key = s.payload.get("step", str(s.id))
        mem = _memory(store, s.id)
        rows.append({"id": s.id, "title": s.title, "agent": _agent_of(s, agents), "state": s.state,
                     "progress": s.progress, "memory_text": memory_text(mem), "memory_bytes": mem or 0,
                     "retries": s.retries, "error": s.error})
        nodes.append({"id": key, "label": s.title[:40], "level": level.get(key, 0), "state": DIAGRAM_STATE[s.state]})
        edges += [[n, key] for n in s.payload.get("needs") or []]
    return rows, {"nodes": nodes, "edges": edges}


def build(store, runner, graphs=None, now: float | None = None, agents=None) -> dict:
    now = time.time() if now is None else now
    active, finished = store.list_for_view(FINISHED_SHOWN)       # never the whole table (M5 audit F6)
    rows = []
    for job in active + finished[:FINISHED_SHOWN]:
        steps, diagram, memory = [], None, 0
        if is_graph(job):
            steps, diagram = _step_rows(store, store.list_jobs(parent_id=job.id), agents)
            memory = sum(s["memory_bytes"] for s in steps)
        else:
            memory = _memory(store, job.id) or 0
        rows.append({"id": job.id, "title": job.title, "agent": _agent_of(job, agents) or job.kind.replace("_", " "),
                     "kind": job.kind, "state": job.state, "started": job.started,
                     "run_seconds": _run_seconds(job, now),
                     "run_text": duration_text(_run_seconds(job, now)) if job.started else "",
                     "progress": job.progress, "error": job.error, "memory_text": memory_text(memory),
                     "graph": is_graph(job), "steps": steps, "diagram": diagram,
                     "controls": controls_for(job, runner, graphs)})
    return {"jobs": rows, "empty": not rows}


def act(store, runner, graphs, job_id: int, action: str) -> tuple[int, str]:
    """Do one button's move. (200, text), or 404 for an unknown job/action and 409 when the move isn't allowed."""
    if action not in ACTIONS:
        return 404, "No such action."
    job = store.get_job(job_id)
    if job is None:
        return 404, "No such job."
    if action not in controls_for(job, runner, graphs):
        return 409, f"That can't be done while it is {job.state}."
    if action == "rerun":
        return 409, "Run again needs the app (it starts the chat's request again)."     # handled in routes
    if is_graph(job):
        done = {"pause": graphs.pause, "resume": graphs.resume, "stop": graphs.cancel}[action](job_id)
    elif action == "stop":
        done = runner.cancel_job(job_id)
        try:
            limits.end_job(job_id)
        except Exception:                              # noqa: BLE001
            pass
    else:
        done = False
    return (200, "Done.") if done else (409, "That changed just now. Look again.")
