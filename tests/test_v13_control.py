"""v1.3: apps, windows, volume, brightness, typing, clicking, power and installs, with the safety rules."""
import pytest

from prometheus.agents.system import SystemAgent, needs_approval, perform
from prometheus.laptop import Laptop, LaptopError, sendkeys_escape


class Run:
    def __init__(self, replies=None):
        self.calls, self.envs = [], []
        self.replies = replies or {}

    def __call__(self, argv, timeout=20, env=None):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        text = " ".join(argv)
        for needle, out in self.replies.items():
            if needle in text:
                return 0, out
        return 0, "ok"

    def scripts(self):
        return [c[-1] for c in self.calls if c[0] == "powershell"]


def lp(replies=None):
    run = Run(replies)
    return Laptop(run=run, platform="win32"), run


# -- nothing you type ever becomes part of a command line --------------------------------
def test_app_name_travels_in_the_environment_not_the_script():
    lap, run = lp()
    lap.app_open("Spotify")
    assert run.envs[-1]["PROM_APP"] == "Spotify" and "Spotify" not in run.scripts()[-1]


@pytest.mark.parametrize("name", ['x"; calc', "a\nb", "", "x" * 61, "app|other", "$(whoami)", "a`b"])
def test_odd_app_names_are_refused(name):
    lap, run = lp()
    with pytest.raises(LaptopError):
        lap.app_open(name)
    assert run.calls == []


@pytest.mark.parametrize("name", ["powershell", "PowerShell.exe", "cmd", "pwsh", "wt", "bash", "wsl", "regedit", "mmc",
                                  "Windows Terminal", "conhost"])
def test_shells_and_admin_tools_are_never_opened(name):
    lap, run = lp()
    with pytest.raises(LaptopError, match="never"):
        lap.app_open(name)
    assert run.calls == []


def test_close_is_gentle_and_never_closes_prometheus():
    lap, run = lp({"CloseMainWindow": "1"})
    assert "closed" in lap.app_close("notepad").lower()
    assert "Kill" not in run.scripts()[-1] and "Stop-Process" not in run.scripts()[-1]
    with pytest.raises(LaptopError):
        lap.app_close("Prometheus")


# -- typing: freely allowed, but never into a terminal, and never a newline ---------------------
def test_sendkeys_escape():
    assert sendkeys_escape("a+b^c%d~e(f)g{h}i[j]") == "a{+}b{^}c{%}d{~}e{(}f{)}g{{}h{}}i{[}j{]}"
    with pytest.raises(LaptopError):
        sendkeys_escape("line one\nline two")
    with pytest.raises(LaptopError):
        sendkeys_escape("x" * 501)


def test_typing_goes_in_the_environment_and_is_refused_in_a_terminal():
    lap, run = lp({"ForegroundProcess": "ForegroundProcess:notepad"})
    lap.type_text("hello + world")
    assert run.envs[-1]["PROM_TEXT"] == "hello {+} world" and "hello" not in run.scripts()[-1]
    for shell in ("WindowsTerminal", "powershell", "cmd", "pwsh", "conhost", "bash", "wsl"):
        lap2, run2 = lp({"ForegroundProcess": f"ForegroundProcess:{shell}"})
        with pytest.raises(LaptopError, match="terminal"):
            lap2.type_text("rm -rf")
        assert not any("SendKeys" in sc and "PROM_TEXT" in sc for sc in run2.scripts())


def test_key_presses_use_a_fixed_list():
    lap, run = lp({"ForegroundProcess": "ForegroundProcess:notepad"})
    lap.press_keys("ctrl+c")
    assert run.envs[-1]["PROM_KEYS"] == "^c"
    with pytest.raises(LaptopError):
        lap.press_keys("ctrl+alt+delete")
    with pytest.raises(LaptopError):
        lap.press_keys("{ENTER}")


# -- what needs a tap on Approvals --------------------------------------------------------------
@pytest.mark.parametrize("action,params", [
    ("press_keys", {"keys": "enter"}), ("press_keys", {"keys": "ctrl+enter"}), ("click", {"x": 10, "y": 10}),
    ("power", {"mode": "shutdown"}), ("power", {"mode": "restart"}), ("power", {"mode": "sleep"}),
    ("power", {"mode": "logoff"}), ("install_app", {"target": "Spotify.Spotify"})])
def test_these_wait_for_approval(action, params):
    assert needs_approval(action, params)


def test_typing_is_asked_first():
    assert needs_approval("type_text", {"text": "hi"})


@pytest.mark.parametrize("action,params", [
    ("app_open", {"target": "notepad"}), ("app_close", {"target": "notepad"}),
    ("press_keys", {"keys": "ctrl+c"}), ("volume", {"level": 30}), ("brightness", {"level": 50}),
    ("dark_mode", {"mode": "on"}), ("screenshot", {}), ("power", {"mode": "lock"}), ("wifi_status", {}),
    ("window", {"target": "Notepad", "mode": "minimize"}), ("apps_running", {})])
def test_these_happen_straight_away(action, params):
    assert needs_approval(action, params) == ""


def test_the_agent_asks_instead_of_pressing_enter(make_ctx):
    ctx = make_ctx()
    lap, run = lp({"ForegroundProcess": "ForegroundProcess:notepad"})
    result = SystemAgent(laptop=lap).run("send", {"action": "press_keys", "keys": "enter"}, ctx)
    assert result.status == "waiting_approval" and run.calls == []
    [a] = ctx.store.approvals()
    assert a.kind == "laptop_action" and a.payload["action"] == "press_keys" and a.payload["keys"] == "enter"


def test_approval_handler_runs_the_same_checked_action():
    lap, run = lp({"ForegroundProcess": "ForegroundProcess:notepad"})
    assert perform("press_keys", {"keys": "enter"}, lap)
    assert run.envs[-1]["PROM_KEYS"] == "{ENTER}"
    with pytest.raises(LaptopError):
        perform("click", {"x": -5, "y": 10}, lap)
    with pytest.raises(LaptopError):
        perform("format_disk", {}, lap)


def test_power_and_install_use_fixed_commands():
    lap, run = lp()
    perform("power", {"mode": "shutdown"}, lap)
    assert run.calls[-1] == ["shutdown", "/s", "/t", "30"]
    perform("power", {"mode": "lock"}, lap)
    assert run.calls[-1][0] == "rundll32.exe"
    perform("install_app", {"target": "Spotify.Spotify"}, lap)
    assert run.calls[-1][:4] == ["winget", "install", "--id", "Spotify.Spotify"] and "-e" in run.calls[-1]
    for bad in ("x; calc", "a b", "--source evil", ""):
        with pytest.raises(LaptopError):
            perform("install_app", {"target": bad}, lap)


# -- the settings actions ------------------------------------------------------------------------
def test_levels_are_numbers_in_the_environment():
    lap, run = lp()
    lap.volume(40)
    assert run.envs[-1]["PROM_LEVEL"] == "40"
    lap.brightness(70)
    assert run.envs[-1]["PROM_LEVEL"] == "70"
    for bad in (-1, 101, "50; calc", None):
        with pytest.raises(LaptopError):
            lap.volume(bad)


def test_dark_mode_screenshot_and_listing():
    lap, run = lp({"AppsUseLightTheme": "ok", "CopyFromScreen": r"C:\Users\me\Pictures\Prometheus\shot.png",
                   "MainWindowTitle": '[{"Name":"notepad","MainWindowTitle":"Untitled - Notepad"}]'})
    assert "dark" in lap.dark_mode("on").lower() and run.envs[-1]["PROM_MODE"] == "0"
    assert lap.screenshot().endswith("shot.png")
    assert lap.apps_running() == [{"name": "notepad", "title": "Untitled - Notepad"}]
    with pytest.raises(LaptopError):
        lap.dark_mode("purple")


def test_not_windows_still_says_so():
    with pytest.raises(LaptopError, match="Windows"):
        Laptop(run=Run(), platform="linux").app_open("notepad")


def test_the_agent_reports_results_and_keeps_them_local(make_ctx):
    ctx = make_ctx()
    lap, _ = lp({"MainWindowTitle": '[{"Name":"notepad","MainWindowTitle":"Taxes 2025 - Notepad"}]'})
    result = SystemAgent(laptop=lap).run("what is open", {"action": "apps_running"}, ctx)
    assert result.status == "done" and "Taxes 2025" in result.summary
    assert SystemAgent.private_outputs is True


@pytest.mark.parametrize("reply", ["", "Get-Process : Cannot find a process", "ForegroundProcess:"])
def test_typing_is_refused_when_the_front_window_is_unknown(reply):
    lap, run = lp({"ForegroundProcess": reply} if reply else {"ForegroundProcess": ""})
    with pytest.raises(LaptopError, match="could not tell"):
        lap.type_text("hello")
    with pytest.raises(LaptopError, match="could not tell"):
        lap.press_keys("ctrl+c")
    with pytest.raises(LaptopError, match="could not tell"):
        lap.click(5, 5)
