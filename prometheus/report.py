"""The weekly report card: is Prometheus actually helping? (From the "Build your first AI agent" guide.)

Five numbers for the last 7 days: how many jobs it did, how many worked, how often it had to ask you, what you
weren't happy with (thumbs down), and what paid AI cost. Plus what went wrong and the practice test result.
Built from the database, with no AI call.
"""
from __future__ import annotations

import time


def week(services, now: float | None = None) -> str:
    store = services.store
    now = time.time() if now is None else now
    since = now - 7 * 86400
    tasks = store._rows("SELECT status, text FROM tasks WHERE created >= ?", (since,))
    done = sum(1 for t in tasks if t["status"] == "done")
    failed = [t["text"][:70] for t in tasks if t["status"] == "failed"]
    asked_steps = store._rows("SELECT COUNT(*) AS n FROM steps WHERE created >= ? AND status = 'waiting_approval'",
                              (since,))[0]["n"]
    approvals = store._rows("SELECT status FROM approvals WHERE created >= ?", (since,))
    said_no = sum(1 for a in approvals if a["status"] in ("rejected", "denied"))
    ratings = store._rows("SELECT rating FROM feedback WHERE created >= ?", (since,))
    good = sum(1 for r in ratings if r["rating"] > 0)
    bad = sum(1 for r in ratings if r["rating"] < 0)
    cost = store.total_cost(since=since)
    lines = [f"## Weekly report card, {time.strftime('%d %b', time.localtime(since))} to "
             f"{time.strftime('%d %b', time.localtime(now))}",
             f"- **Jobs:** {len(tasks)} asked, {done} done"
             + (f" ({round(100 * done / len(tasks))}%)" if tasks else "") + f", {len(failed)} didn't work out.",
             f"- **Asked you first:** {asked_steps + len(approvals)} times"
             + (f"; you said no {said_no} times." if said_no else "."),
             f"- **Your ratings:** {good} 👍, {bad} 👎." + (" Tell me what was wrong with the 👎 ones and I'll learn it."
                                                         if bad else ""),
             f"- **Paid AI cost:** ${cost:.2f} this week."]
    if failed:
        lines.append("\n**Didn't work out**\n" + "\n".join(f"- {t}" for t in failed[:5]))
    try:
        from prometheus.practice import last_result

        result = last_result(store)
    except Exception:
        result = ""
    if result:
        lines.append(f"\n**Practice tests:** {result}")
    if tasks and done / len(tasks) < 0.8:
        lines.append("\nFewer than 8 in 10 jobs worked. The practice tests (Settings → Advanced) show which kinds "
                     "fail; turn a failed job into a test with \"save this as a practice test\".")
    return "\n".join(lines)
