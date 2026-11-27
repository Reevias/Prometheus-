"""CloudroomRuntime (M6 step 6.2): runs a task on the owner's free Render service, behind the same five runtime calls.

Safety, all in code:
- Moving asks first (Approvals page). The question shows what goes up (each file's name and size, the code, the
  limits) and what it may do there (run Python in a sandbox, no internet, at most N seconds and M MB). The yes is tied
  to exactly that (a digest of the code, the files' contents and the limits) and works once; a file changed after the
  yes is refused.
- Only the Cloudroom token signs requests (it never travels itself). A task holding one of the owner's other keys is
  refused before anything goes up, and no other key is ever read for it.
- A task that doesn't fit the free machine (512 MB) stays on the laptop, with the reason.
- Every move has a request id; the server's signed receipt is checked (and that it received exactly what was sent).
  Events are numbered and put in order (duplicates dropped, gaps fetched again).
- A lost link is retried. Once a receipt is held the task is never sent again: if Cloudroom restarted and forgot it,
  the task is marked failed. Before a receipt, a retry sends the same request id, which the server never runs twice.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING, TERMINAL_STATES
from prometheus.jobs.store import JobStore
from prometheus.runtime import cloudroom_protocol as proto
from prometheus.runtime.base import ExecutionRuntime, JobSpec, RuntimeRefused
from prometheus.runtime.cloudroom_server import MAX_MEMORY_MB, MAX_SECONDS, MAX_UPLOAD, SAFE_NAME, task_digest

APPROVAL_KIND = "cloudroom_move"
TOKEN_NAME = "CLOUDROOM_TOKEN"
DEFAULT_SECONDS, DEFAULT_MEMORY_MB = 60, 128
GIVE_UP_AFTER = 600                # seconds without any contact before a moved task is marked failed


def _other_secrets() -> list[str]:
    """The owner's other keys, only to make sure none of them goes up (they are compared, never sent)."""
    try:
        from prometheus.safety.secrets import known_secret_values

        return [v for v in known_secret_values() if isinstance(v, str) and len(v) >= 8]
    except Exception:                                      # noqa: BLE001
        return []


def _token_from_keys() -> str:
    from prometheus.config import get_secret

    return get_secret(TOKEN_NAME) or ""


class CloudroomRuntime(ExecutionRuntime):
    name = "cloudroom"

    def __init__(self, store: JobStore, http, token: Callable[[], str] = _token_from_keys, poll: float = 2.0,
                 waits: tuple = (2, 5, 15, 30), give_up_after: float = GIVE_UP_AFTER):
        self.store = store
        self.http = http                                   # httpx.Client(base_url=the Render address) in the app
        self._token = token
        self.poll = poll
        self.waits = tuple(waits) or (2,)
        self.give_up_after = give_up_after
        self.runner = JobRunner(store)
        self._yes: dict[str, str] = {}                     # move id -> digest of exactly what the owner said yes to
        self._approved: dict[str, str] = {}                # a yes just used -> its digest, checked again in start()
        self._gen: dict[int, int] = {}                     # job id -> which watcher may follow it (M6 audit A8)
        self._stop: set[int] = set()
        self._lock = threading.Lock()
        self._local = threading.local()                    # which watcher generation this thread is

    # -- what goes up, checked and described (item 2 and the fit check of item 5) -----------------------------------
    def _gather(self, spec: JobSpec) -> tuple[dict, dict, str]:
        """(what the owner sees, the files to send, the digest). Raises RuntimeRefused with a plain reason."""
        cloud = dict((spec.payload or {}).get("cloud") or {})
        if cloud.get("kind", "python") != "python":
            raise RuntimeRefused("Only Python tasks can move to Cloudroom.")
        code = cloud.get("code")
        if not isinstance(code, str) or not code.strip():
            raise RuntimeRefused("There is no code to run in Cloudroom.")
        seconds = int(cloud.get("seconds") or DEFAULT_SECONDS)
        memory = int(cloud.get("memory_mb") or DEFAULT_MEMORY_MB)
        if cloud.get("network"):
            raise RuntimeRefused("Tasks in Cloudroom have no internet, so this one stays on the laptop.")
        if memory > MAX_MEMORY_MB:
            raise RuntimeRefused(f"This task needs {memory} MB of memory; Cloudroom's free machine can give a task "
                                 f"{MAX_MEMORY_MB} MB, so it stays on the laptop.")
        if not 1 <= seconds <= MAX_SECONDS:
            raise RuntimeRefused(f"This task needs {seconds} seconds of time; Cloudroom allows {MAX_SECONDS}, so it "
                                 "stays on the laptop.")
        files_meta, files_b64, total = {}, {}, 0
        for name, path in (cloud.get("files") or {}).items():
            if not isinstance(name, str) or not SAFE_NAME.match(name):
                raise RuntimeRefused(f"'{name}' isn't a file name Cloudroom accepts.")
            data = Path(path).read_bytes()
            total += len(data)
            if total > MAX_UPLOAD:
                raise RuntimeRefused(f"The files are over {MAX_UPLOAD // (1024 * 1024)} MB, so the task stays on the "
                                     "laptop.")
            files_meta[name] = {"path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            files_b64[name] = base64.b64encode(data).decode()
        texts = [code] + [base64.b64decode(v).decode("utf-8", "replace") for v in files_b64.values()]
        for secret in _other_secrets():
            if any(secret in t for t in texts):
                raise RuntimeRefused("This task holds one of your keys, and keys never go to Cloudroom.")
        shown = {"kind": "python", "code": code, "seconds": seconds, "memory_mb": memory, "network": False,
                 "files": files_meta}
        digest = hashlib.sha256(json.dumps({"title": spec.title, **shown,
                                            "files": {n: m["sha256"] for n, m in files_meta.items()}},
                                           sort_keys=True).encode()).hexdigest()
        return shown, files_b64, digest

    def describe(self, spec: JobSpec) -> str:
        """The question the owner sees: what goes up and what it may do there."""
        shown, _, _ = self._gather(spec)
        files = "; ".join(f"{n} ({m['size']} bytes)" for n, m in shown["files"].items()) or "none"
        code = shown["code"]                               # all of it: what you approve is what goes (M6 audit A10)
        return (f"Move '{spec.title}' to Cloudroom (your free Render service)?\n"
                f"Files that go up: {files}.\nCode that goes up:\n{code}\n"
                f"There it runs Python in a sandbox with no internet, for at most {shown['seconds']} seconds and "
                f"{shown['memory_mb']} MB of memory. None of your keys goes up.")

    def move_payload(self, spec: JobSpec) -> dict:
        shown, _, digest = self._gather(spec)
        return {"title": spec.title, "kind": spec.kind, "cloud": shown, "digest": digest}

    # -- the owner's yes -----------------------------------------------------------------------------------------
    def request_move(self, approvals, spec: JobSpec) -> int:
        """Ask on the Approvals page. Nothing goes up until the owner says yes."""
        payload = {**self.move_payload(spec), "move_id": uuid.uuid4().hex}
        return approvals.request(APPROVAL_KIND, self.describe(spec), payload)

    def connect_approvals(self, approvals, runtimes) -> None:
        """A yes on the Approvals page records the yes for exactly that move and starts it."""
        def on_yes(payload: dict) -> str:
            move_id = str(payload.get("move_id") or "")
            self._record_yes(move_id, payload)
            cloud = dict(payload["cloud"])
            cloud["files"] = {n: m["path"] for n, m in (cloud.get("files") or {}).items()}
            try:
                job = runtimes.start(JobSpec(payload.get("kind") or "cloudroom", payload["title"],
                                             payload={"cloud": cloud}, runtime=self.name, approval=move_id))
            except Exception:
                with self._lock:
                    self._yes.pop(move_id, None)           # a move that didn't happen keeps no yes (M6 audit A11)
                raise
            return f"Moved to Cloudroom (job {job.id})."
        approvals.register(APPROVAL_KIND, on_yes)

    def _record_yes(self, move_id: str, payload: dict) -> None:
        if move_id and payload.get("digest"):
            with self._lock:
                self._yes[move_id] = payload["digest"]

    def check_yes(self, approval: str, spec: JobSpec) -> bool:
        """The yes exists, is for exactly this move (files unchanged), and is used up now."""
        with self._lock:
            want = self._yes.get(approval or "")
        if want is None:
            return False
        _, _, digest = self._gather(spec)
        if digest != want:
            raise RuntimeRefused("Something changed after your yes (a file or the code), so nothing was moved. Ask "
                                 "again to see what would go up now.")
        with self._lock:
            if self._yes.pop(approval, None) is None:
                return False
            self._approved[approval] = digest              # start() sends only this exact content (M6 audit A9)
            return True

    # -- the five runtime calls ------------------------------------------------------------------------------------
    def start(self, spec: JobSpec):
        shown, files_b64, digest = self._gather(spec)
        with self._lock:
            approved = self._approved.pop(spec.approval or "", None)
        if approved is None or approved != digest:
            raise RuntimeRefused("Something changed after your yes (a file or the code), so nothing was moved. Ask "
                                 "again to see what would go up now.")
        public = {**shown, "files": {n: {"size": m["size"], "sha256": m["sha256"]} for n, m in shown["files"].items()}}
        job = self.store.create_job(kind=spec.kind or "cloudroom", title=spec.title, parent_id=spec.parent_id,
                                    payload={"where": self.name, "request_id": uuid.uuid4().hex, "cloud": public,
                                             "digest": digest, "remote_seen": []})
        self._watch_in_background(job.id, files_b64)
        return self.store.get_job(job.id)

    def status(self, job_id: int):
        return self.store.get_job(job_id)

    def cancel(self, job_id: int, reason: str = "Cancelled by you") -> bool:
        job = self.store.get_job(job_id)
        if job is None or job.state in TERMINAL_STATES:
            return False
        receipt = job.payload.get("receipt")
        if receipt:
            try:
                self._call("POST", f"/v1/tasks/{receipt['task_id']}/cancel", b"")
            except Exception:                              # noqa: BLE001 - the laptop's record is cancelled anyway
                pass
        self.store.set_state(job_id, CANCELLED, error=reason)
        return True

    def events(self, job_id: int, after: int = 0) -> list[dict]:
        return self.store.events(job_id, after)

    def result(self, job_id: int) -> Any:
        job = self.store.get_job(job_id)
        return job.payload.get("result") if job is not None else None

    # -- recovery after the laptop restarted --------------------------------------------------------------------------
    def resume(self, job_id: int) -> bool:
        """Carry on following a moved task after a restart. With a receipt it is never sent again."""
        job = self.store.get_job(job_id)
        if job is None or job.state != PAUSED or job.payload.get("where") != self.name:
            return False
        self.store.set_state(job_id, PENDING)
        self._watch_in_background(job_id, None)
        return True

    def stop_watching(self, job_id: int) -> None:
        """Stop following a task in this process (as when Prometheus closes); the task itself isn't touched."""
        with self._lock:
            self._stop.add(job_id)

    # -- inside ------------------------------------------------------------------------------------------------------
    def _call(self, method: str, path: str, body: bytes = b""):
        token = self._token()
        if not token:
            raise RuntimeRefused("Cloudroom has no key yet: add the Cloudroom key in Settings.")
        headers = {"Content-Type": "application/json", **proto.sign_headers(token, method, path, body)}
        return self.http.request(method, path, headers=headers, content=body or None)

    def _watch_in_background(self, job_id: int, files_b64: dict | None) -> None:
        with self._lock:
            self._stop.discard(job_id)
            gen = self._gen.get(job_id, 0) + 1             # an older watcher of this job stops at its next look
            self._gen[job_id] = gen
        self.runner.run_existing(job_id, self._watch_safely, args=(job_id, files_b64, gen), name=f"cloudroom-{job_id}",
                                 mark_running=False)

    def _watch_safely(self, job_id: int, files_b64: dict | None, gen: int) -> None:
        """Follow the task; anything unexpected marks it failed instead of leaving it 'running' (M6 audit A4)."""
        self._local.gen = gen
        try:
            self._watch(job_id, files_b64)
        except RuntimeRefused as e:
            if not self._stopped(job_id):
                self._fail(job_id, str(e))
        except Exception as e:                             # noqa: BLE001
            if not self._stopped(job_id):
                self._fail(job_id, f"Following the task in Cloudroom broke ({type(e).__name__}); it isn't sent again.")

    def _stopped(self, job_id: int) -> bool:
        with self._lock:
            if job_id in self._stop:
                return True
            if self._gen.get(job_id) != getattr(self._local, "gen", self._gen.get(job_id)):
                return True                                # a newer watcher took over
        job = self.store.get_job(job_id)
        return job is None or job.state not in (PENDING, RUNNING)

    def _fail(self, job_id: int, why: str) -> None:
        self.store.finish(job_id, FAILED, error=why)

    def _watch(self, job_id: int, files_b64: dict | None) -> None:
        job = self.store.get_job(job_id)
        receipt = job.payload.get("receipt")
        if receipt is None:
            if files_b64 is None:                          # after a restart, before any receipt: read the files again
                try:
                    _, files_b64, digest = self._gather(JobSpec(job.kind, job.title, payload={"cloud": {
                        **job.payload["cloud"], "files": {}}}))
                except RuntimeRefused as e:
                    self._fail(job_id, str(e))
                    return
                if job.payload["cloud"].get("files"):
                    self._fail(job_id, "Prometheus restarted before Cloudroom confirmed the task; its files aren't "
                                       "kept on the laptop for a resend, so ask again.")
                    return
            receipt = self._submit(job_id, job, files_b64)
            if receipt is None:
                return
        if self._stopped(job_id):
            return
        if self.store.get_job(job_id).state == PENDING:
            self.store.set_state(job_id, RUNNING, progress="Running in Cloudroom")
        self._follow(job_id, receipt)

    def _body(self, job, files_b64: dict) -> tuple[bytes, dict]:
        cloud = job.payload["cloud"]
        task = {"request_id": job.payload["request_id"], "kind": "python", "code": cloud["code"],
                "files": files_b64 or {}, "seconds": cloud["seconds"], "memory_mb": cloud["memory_mb"],
                "network": False}
        return json.dumps(task).encode(), task

    def _submit(self, job_id: int, job, files_b64: dict) -> dict | None:
        """Send the task under its request id until a receipt comes back (the server never runs an id twice)."""
        body, task = self._body(job, files_b64)
        for attempt in range(len(self.waits) + 1):
            if self._stopped(job_id):
                return None
            try:
                resp = self._call("POST", "/v1/tasks", body)
            except RuntimeRefused as e:
                self._fail(job_id, str(e))
                return None
            except httpx.HTTPError:
                if attempt < len(self.waits):
                    time.sleep(self.waits[attempt])
                continue
            if resp.status_code == 200:
                receipt = resp.json()
                token = self._token()
                if not proto.verify_receipt(token, receipt) or receipt.get("request_id") != task["request_id"] \
                        or receipt.get("digest") != task_digest(task):
                    self._fail(job_id, "Cloudroom's receipt didn't match what was sent, so the task was stopped.")
                    return None
                with self.store._lock:                     # receipt and "running" together (M6 audit A3)
                    self.store.merge_payload(job_id, receipt=receipt, remote_seen=[])
                    if self.store.get_job(job_id).state == PENDING:
                        self.store.set_state(job_id, RUNNING, progress="Running in Cloudroom")
                return receipt
            why = (resp.json() or {}).get("error", "") if resp.headers.get("content-type", "").startswith(
                "application/json") else ""
            self._fail(job_id, "Cloudroom refused the key (check the Cloudroom key in Settings)." if
                       resp.status_code == 401 else f"Cloudroom didn't take the task: {why or resp.status_code}.")
            return None
        self._fail(job_id, "Couldn't reach Cloudroom (no answer after several tries); the task wasn't run there.")
        return None

    def _follow(self, job_id: int, receipt: dict) -> None:
        """Read the task's events in order until it ends; a lost link is retried, a restart of Cloudroom fails it."""
        task_id, boot = receipt["task_id"], receipt["boot_id"]
        last_contact, misses = time.monotonic(), 0
        while not self._stopped(job_id):
            seen = list(self.store.get_job(job_id).payload.get("remote_seen") or [])
            last = seen[-1] if seen else 0
            try:
                resp = self._call("GET", f"/v1/tasks/{task_id}/events?after={last}")
            except httpx.HTTPError:
                misses += 1
                if time.monotonic() - last_contact > self.give_up_after:
                    self._fail(job_id, "Lost contact with Cloudroom; the task may have run there, but it isn't sent "
                                       "again. Ask again if you need it.")
                    return
                time.sleep(self.waits[min(misses, len(self.waits)) - 1])
                continue
            data = {}
            if resp.headers.get("content-type", "").startswith("application/json"):
                try:
                    data = resp.json()
                except ValueError:
                    data = {}
            if resp.status_code == 401:
                self._fail(job_id, "Cloudroom refused the signature: check the Cloudroom key in Settings and the "
                                   "laptop's clock (it must be within 5 minutes). The task isn't sent again.")
                return
            # only a real answer from Cloudroom with another boot id means it restarted; a proxy page, a 5xx or a
            # 429 is retried until GIVE_UP_AFTER (M6 audit A2)
            if (resp.status_code == 404 and data.get("boot_id") and data["boot_id"] != boot) or \
                    (resp.status_code == 200 and data.get("boot_id") != boot):
                self._fail(job_id, "Cloudroom restarted and forgot this task, so it was lost there. It isn't sent "
                                   "again; ask again if you need it.")
                return
            if resp.status_code != 200:
                if time.monotonic() - last_contact > self.give_up_after:
                    self._fail(job_id, "Cloudroom kept answering with errors; the task isn't sent again.")
                    return
                misses += 1
                time.sleep(self.waits[min(misses, len(self.waits)) - 1])
                continue
            last_contact, misses = time.monotonic(), 0
            final = None
            for ev in sorted({e["seq"]: e for e in data.get("events", [])}.values(), key=lambda e: e["seq"]):
                if ev["seq"] <= last:
                    continue                               # already had it
                if ev["seq"] != last + 1:
                    break                                  # a gap: read again from the last one in order
                last = ev["seq"]
                seen.append(last)
                if ev["state"] in ("done", "failed", "cancelled"):
                    final = ev
                    break
            self.store.merge_payload(job_id, remote_seen=seen)
            if final is not None:
                self._finish(job_id, task_id, final)
                return
            time.sleep(self.poll)

    def _finish(self, job_id: int, task_id: str, final: dict) -> None:
        if final["state"] == "cancelled":
            if self.store.get_job(job_id).state not in TERMINAL_STATES:
                self.store.set_state(job_id, CANCELLED, error="Cancelled in Cloudroom")
            return
        result = None
        for attempt in range(len(self.waits) + 1):
            try:
                resp = self._call("GET", f"/v1/tasks/{task_id}/result")
                if resp.status_code == 200:
                    result = resp.json()
                    break
            except httpx.HTTPError:
                pass
            if attempt < len(self.waits):
                time.sleep(self.waits[attempt])
        if result is None:
            self._fail(job_id, "Cloudroom finished the task but its result couldn't be fetched.")
            return
        kept = {"ok": bool(result.get("ok")), "output": str(result.get("output", ""))[:20000],
                "timed_out": bool(result.get("timed_out")), "files": sorted((result.get("files") or {}).keys())}
        self.store.merge_payload(job_id, result=kept)
        if self.store.get_job(job_id).state == RUNNING:
            if final["state"] == "done":
                self.store.set_state(job_id, DONE, progress="")
            else:
                self.store.set_state(job_id, FAILED, error=(final.get("text") or kept["output"])[:500])
