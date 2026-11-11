"""The weekly laptop health check (Sunday evening, or "check my laptop"): disk space, big old files, startup apps,
Windows updates waiting, battery wear, whether backups ran, and temporary files. Each problem comes with a fix
button: opening the right Windows Settings page is asked in the chat; deleting things waits on the Approvals page.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

CHAT = "Laptop health"
BIG = 300 * 1024 * 1024
OLD_DAYS = 120
TEMP_DAYS = 7
SKIP = {"node_modules", ".git", "appdata", "$recycle.bin", "windows", "program files", "program files (x86)"}


def big_old_files(home: Path, now: float, limit: int = 15) -> list[tuple[Path, int]]:
    found = []
    deadline = time.monotonic() + 60
    for top in ("Downloads", "Desktop", "Documents", "Videos"):
        stack = [home / top]
        while stack and time.monotonic() < deadline:
            folder = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_symlink() or e.name.startswith("."):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if e.name.lower() not in SKIP:
                            stack.append(Path(e.path))
                    else:
                        st = e.stat()
                        if st.st_size >= BIG and now - st.st_mtime > OLD_DAYS * 86400:
                            found.append((Path(e.path), st.st_size))
                except OSError:
                    continue
    return sorted(found, key=lambda x: -x[1])[:limit]


def temp_size(now: float) -> tuple[int, int]:
    """(bytes, files) of temporary files older than a week."""
    total = count = 0
    for p in Path(tempfile.gettempdir()).glob("*"):
        try:
            st = p.stat()
            if p.is_file() and now - st.st_mtime > TEMP_DAYS * 86400:
                total += st.st_size
                count += 1
        except OSError:
            continue
    return total, count


def clear_temp(now: float | None = None) -> str:
    now = time.time() if now is None else now
    freed = n = 0
    for p in Path(tempfile.gettempdir()).glob("*"):
        try:
            st = p.stat()
            if p.is_file() and now - st.st_mtime > TEMP_DAYS * 86400:
                p.unlink()
                freed += st.st_size
                n += 1
        except OSError:
            continue                           # in use: left alone
    return f"Cleared {n} old temporary files ({_gb(freed)})."


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def check(services, laptop=None, now: float | None = None) -> tuple[str, list[dict]]:
    """(report, fixes). Each fix: {'label', 'kind', 'payload', 'big'}."""
    now = time.time() if now is None else now
    home = Path(services.home)
    lines, fixes = [], []
    usage = shutil.disk_usage(home.anchor or "/")
    free_pct = 100 * usage.free / usage.total
    disk = f"Disk: {_gb(usage.free)} free of {_gb(usage.total)} ({free_pct:.0f}%)"
    if free_pct < 15:
        disk += " — low"
        fixes.append({"label": "Open Storage settings (Storage Sense can clean up)", "kind": "health_fix",
                      "payload": {"fix": "settings", "page": "storage"}, "big": False})
    lines.append(disk)
    big = big_old_files(home, now)
    if big:
        total = sum(s for _, s in big)
        lines.append(f"Big files untouched for {OLD_DAYS}+ days: {len(big)} ({_gb(total)})\n" +
                     "\n".join(f"  - {p.name} ({_gb(s)}, {p.parent.name})" for p, s in big[:8]))
        fixes.append({"label": f"Send these {len(big)} big old files to the Recycle Bin ({_gb(total)})",
                      "kind": "recycle_files", "payload": {"paths": [str(p) for p, _ in big]}, "big": True})
    tbytes, tcount = temp_size(now)
    if tbytes > 200 * 1024 ** 2:
        lines.append(f"Old temporary files: {tcount} ({_gb(tbytes)})")
        fixes.append({"label": f"Clear old temporary files ({_gb(tbytes)})", "kind": "health_clean",
                      "payload": {"fix": "temp"}, "big": True})
    info = {}
    if laptop is not None:
        try:
            info = laptop.health_info()
        except Exception:
            info = {}
    startup = info.get("startup") or []
    if startup:
        lines.append(f"Apps that start with Windows: {len(startup)} ({', '.join(startup[:8])}"
                     f"{'…' if len(startup) > 8 else ''})")
        if len(startup) > 6:
            fixes.append({"label": "Open Startup apps (turn off the ones you don't need)", "kind": "health_fix",
                          "payload": {"fix": "settings", "page": "startup"}, "big": False})
    if isinstance(info.get("updates"), int) and info["updates"] > 0:
        lines.append(f"Windows updates waiting: {info['updates']}")
        fixes.append({"label": "Open Windows Update", "kind": "health_fix",
                      "payload": {"fix": "settings", "page": "updates"}, "big": False})
    if info.get("design") and info.get("full"):
        health = round(100 * info["full"] / info["design"])
        lines.append(f"Battery health: {health}% of new" + (f", {info['cycles']} charge cycles" if info.get("cycles")
                                                             else "") + (" — worn, consider a replacement"
                                                                         if health < 60 else ""))
    backups = []
    if info.get("onedrive") is not None:
        backups.append("OneDrive is running" if info["onedrive"] else "OneDrive isn't running")
    if info.get("fh"):
        backups.append("File History is set up")
    if backups:
        lines.append("Backups: " + ", ".join(backups))
        if not info.get("onedrive") and not info.get("fh"):
            fixes.append({"label": "Open Backup settings", "kind": "health_fix",
                          "payload": {"fix": "settings", "page": "backup"}, "big": False})
    head = "Weekly laptop health check" + (": all good." if not fixes else f": {len(fixes)} thing(s) to look at.")
    return head + "\n\n" + "\n".join(f"- {l}" for l in lines), fixes


def post(services, laptop=None, now: float | None = None) -> str:
    """Run the check, post it in the Laptop health chat, and offer each fix."""
    store = services.store
    chat = None
    for chat_id, title in store.chats():
        if title == CHAT:
            chat = chat_id
    if chat is None:
        chat = store.create_chat(CHAT)
        store.rename_chat(chat, CHAT, by="user")
    report, fixes = check(services, laptop, now)
    store.add_message(chat, "assistant", report, "health", private=True)
    for f in fixes:
        if f["big"]:
            services.approvals.request(f["kind"], f"Laptop health: {f['label']}", f["payload"])
        else:
            services.approvals.ask(f["kind"], f["label"], f["payload"], (f"health:{time.time()}:{f['label']}", ""),
                                   chat_id=chat)
    store.kv_set("health_last", time.time() if now is None else now)
    return report
