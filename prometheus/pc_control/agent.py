"""Registered agent wrapper for safe desktop inspection & input (M6 6.4a)."""
from __future__ import annotations

from pathlib import Path

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.pc_control.controller import PCController, check_safe_text
from prometheus.pc_control.errors import PCControlError
from prometheus.pc_control.uia import abort_active_ps
from prometheus.pc_control.win32 import normalize_combo

READ_ACTIONS = frozenset({"screenshot", "read_controls", "list_windows"})
INPUT_ACTIONS = frozenset({"click", "type", "press_keys", "switch_app"})


def make_controller(data_dir: Path, killswitch=None) -> PCController:
    return PCController(data_dir=Path(data_dir), killswitch=killswitch)


def _format_controls(res: dict) -> str:
    win = res.get("window") or "(desktop)"
    focused = res.get("focused")
    lines = [f"Window: {win}"]
    if focused:
        pwd_tag = " [PASSWORD]" if focused.get("is_password") else ""
        lines.append(f"Focused: {focused.get('control_type', '')} {focused.get('name', '')!r}{pwd_tag}")
    for c in (res.get("controls") or [])[:60]:
        flags = []
        if c.focused:
            flags.append("focused")
        if not c.enabled:
            flags.append("disabled")
        if c.is_password:
            flags.append("PASSWORD")
        val = f" value={c.value!r}" if c.value and not c.is_password else ""
        flag_s = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"- {c.control_type} {c.name!r} rect={c.rect}{flag_s}{val}")
    return untrusted("\n".join(lines), "pc_screen")


def _execute_read(payload: dict, killswitch=None) -> str:
    ctrl = make_controller(Path(payload["data_dir"]), killswitch=killswitch)
    action = payload["action"]
    if action == "screenshot":
        shot = ctrl.screenshot()
        return f"Screenshot captured ({shot.width}x{shot.height}): {shot.path}"
    if action == "read_controls":
        res = ctrl.read_controls(window_title=payload.get("window_title", ""), limit=int(payload.get("limit", 80)))
        return _format_controls(res)
    if action == "list_windows":
        wins = ctrl.list_windows()
        if not wins:
            return "No visible windows found."
        lines = [f"- {w.title} ({w.process or 'app'}) rect={w.rect}" for w in wins[:40]]
        return untrusted("\n".join(lines), "pc_windows")
    raise PCControlError(f"Unsupported read action: {action!r}")


def _execute_input(payload: dict, killswitch=None) -> str:
    ctrl = make_controller(Path(payload["data_dir"]), killswitch=killswitch)
    action = payload["action"]
    if action == "click":
        out = ctrl.click(
            int(payload["x"]),
            int(payload["y"]),
            button=str(payload.get("button", "left")),
            clicks=int(payload.get("clicks", 1)),
        )
        return f"Clicked {out['button']} ({out['clicks']}x) at ({out['x']}, {out['y']})."
    if action == "type":
        out = ctrl.type_text(str(payload["text"]))
        return f"Typed {out['typed']} character(s) into active window."
    if action == "press_keys":
        out = ctrl.press_keys(str(payload["keys"]))
        return f"Pressed keys: {out['keys']}."
    if action == "switch_app":
        win = ctrl.switch_app(str(payload["app"]))
        return f"Switched foreground window to {win.title!r}."
    raise PCControlError(f"Unsupported input action: {action!r}")


def register_approvals(approvals, killswitch=None) -> None:
    """Register pc_control approval handlers on an Approvals instance if missing."""
    handlers = getattr(approvals, "_handlers", None)
    ks = killswitch if killswitch is not None else getattr(approvals, "killswitch", None)
    if ks is not None and hasattr(ks, "on_stop") and not getattr(approvals, "_pc_stop_hooked", False):
        ks.on_stop(lambda _reason="": abort_active_ps())
        try:
            setattr(approvals, "_pc_stop_hooked", True)
        except Exception:
            pass
    if handlers is None or "pc_control_read" not in handlers:
        approvals.register("pc_control_read", lambda payload: _execute_read(payload, killswitch=ks))
    if handlers is None or "pc_control_input" not in handlers:
        approvals.register("pc_control_input", lambda payload: _execute_input(payload, killswitch=ks))


def _describe_input(action: str, inputs: dict) -> tuple[str, dict]:
    if action == "click":
        if "x" not in inputs or "y" not in inputs:
            raise PCControlError("Click requires 'x' and 'y' screen coordinates.")
        x, y = int(inputs["x"]), int(inputs["y"])
        btn = str(inputs.get("button", "left")).lower()
        clicks = int(inputs.get("clicks", 1))
        summary = f"Click {btn} ({clicks}x) at ({x}, {y}) on the PC screen"
        return summary, {"action": "click", "x": x, "y": y, "button": btn, "clicks": clicks}

    if action == "type":
        text = str(inputs.get("text") or "")
        check_safe_text(text)
        preview = text if len(text) <= 40 else text[:37] + "..."
        summary = f"Type text ({len(text)} chars) into active window: {preview!r}"
        return summary, {"action": "type", "text": text}

    if action == "press_keys":
        keys = str(inputs.get("keys") or "")
        canon, _ = normalize_combo(keys)
        summary = f"Press key combination on PC: {canon}"
        return summary, {"action": "press_keys", "keys": canon}

    if action == "switch_app":
        app = str(inputs.get("app") or "").strip()
        if not app:
            raise PCControlError("switch_app requires 'app' name or window title.")
        summary = f"Switch PC active window to {app!r}"
        return summary, {"action": "switch_app", "app": app}

    raise PCControlError(f"Unknown pc_control action: {action!r}")


@register
class PCControlAgent(Agent):
    name = "pc_control"
    description = (
        "Inspect the laptop screen (screenshot, Windows UI controls, windows) or perform mouse/keyboard/"
        "app-switch actions with explicit owner approval."
    )
    tools = {"system_read", "system_change"}
    required_inputs = ("action",)
    private_outputs = True
    accepts_private = False

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        if ctx.killswitch and ctx.killswitch.is_set:
            return StepResult("failed", "Stopped by Stop all.")

        action = str(inputs.get("action") or "").strip().lower()
        if action not in READ_ACTIONS and action not in INPUT_ACTIONS:
            return StepResult(
                "failed",
                f"Unknown pc_control action {action!r}; choose screenshot, read_controls, list_windows, click, type, press_keys, or switch_app.",
            )

        register_approvals(ctx.approvals, killswitch=ctx.killswitch)
        data_dir = str(ctx.home)

        if action in READ_ACTIONS:
            ctx.use(self.name, "system_read")
            payload = {"action": action, "data_dir": data_dir}
            if action == "read_controls":
                payload["window_title"] = str(inputs.get("window_title") or "")
                payload["limit"] = int(inputs.get("limit") or 80)
            summaries = {
                "screenshot": "Take a screenshot of the PC screen",
                "read_controls": "Read UI controls from the PC screen",
                "list_windows": "List open windows on the PC",
            }
            return ctx.ask(
                "pc_control_read",
                summaries[action],
                payload,
                exact="pc_read",
                broad="pc_read",
                broad_label="Reading the PC screen & controls",
            )

        ctx.use(self.name, "system_change")
        try:
            summary, action_payload = _describe_input(action, inputs)
        except PCControlError as exc:
            return StepResult("failed", str(exc))

        payload = {**action_payload, "data_dir": data_dir}
        aid = ctx.approvals.request("pc_control_input", summary, payload)
        return StepResult("waiting_approval", f"Approval #{aid} required: {summary}", {"approval_id": aid})
