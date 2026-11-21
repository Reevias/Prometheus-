"""Bugs from the owner's laptop screenshots (21 Nov 2026). Each test failed before its fix."""
from __future__ import annotations

from prometheus.orchestrator.loop import approval_resolved
from prometheus.orchestrator.planner import paused_run
from test_v3_m3_audit_b import build
from test_v3_m3_loop import COMPLEX, act, done


def test_dont_allow_ends_the_waiting_task_instead_of_asking_again(make_ctx):
    """Pressing Don't allow made the loop carry on, and its AI asked for the same script again: a new card that looked
    exactly like the old one, so the button seemed to do nothing. A no now ends that task."""
    o, ctx, cloud = build(make_ctx, [act("asker", "send it"), act("asker", "send it"), done("x")])
    chat = ctx.store.create_chat("t")
    out = o.run(chat, COMPLEX)
    assert out.status == "paused"
    [approval] = ctx.store.approvals()
    ready = []
    ctx.approvals.on_resolved.append(lambda i, st, res: ready.extend(approval_resolved(ctx.store, i, st, res)))
    ctx.approvals.resolve(approval.id, False)
    assert ready == []                                               # nothing carries on after a no
    assert paused_run(ctx.store, out.task_id) is None
    assert ctx.store.task(out.task_id)["status"] == "stopped"
    assert ctx.store.approval(approval.id).status == "rejected"
    assert ctx.store.approvals() == []                               # and nothing new is asked
    assert len(cloud.calls) == 1


def test_one_microphone_button_in_the_top_bar(app_client, monkeypatch):
    """Two identical microphone icons sat next to Stop all: Voice on/off (#mic-toggle) and the older Mute (#mute),
    shown while voice listens. When voice is set up there is one button; Ctrl+Shift+M still mutes."""
    import re

    from conftest import login

    client, services = app_client()
    login(client)
    monkeypatch.setattr(services.voice, "ready", lambda: True)
    page = client.get("/").text
    tools = page.split('class="topbar-tools"', 1)[1].split("</header>", 1)[0]
    assert 'id="mic-toggle"' in tools
    assert len(re.findall(r'<button[^>]*class="icon-btn[^"]*(mic-toggle|mute)', tools)) == 1


def test_the_skills_library_key_can_be_entered_in_settings(app_client):
    """M4.5 added the "GitHub skills library" key, but Settings → Connections → Other keys only offered the GitHub
    key, so the owner couldn't find anywhere to paste it."""
    from conftest import login

    client, services = app_client()
    login(client)
    page = client.get("/settings").text
    keys = page.split('id="other-keys"', 1)[1].split("</section>", 1)[0]
    assert 'value="SKILLS_LIBRARY_TOKEN"' in keys and "GitHub skills library" in keys
    assert 'value="GITHUB_TOKEN"' in keys


def test_a_script_that_only_prints_words_is_said_not_asked(make_ctx):
    """Owner's screenshots: "Run this Python script: print('Conversation acknowledged. No tool needed…')?" The AI used
    the sandbox just to say something. A script that is only print() of fixed text is said straight away; nothing
    runs and nothing is asked."""
    from prometheus.agents.code_agent import CodeAgent

    ctx = make_ctx()
    res = CodeAgent().run("say it", {"code": "print('I\\'m not sure what you mean: could you clarify?')"}, ctx)
    assert res.status == "done" and res.summary == "I'm not sure what you mean: could you clarify?"
    assert ctx.store.approvals() == []
    res = CodeAgent().run("say it", {"code": "# note\nprint('Line one')\nprint('Line', 2)"}, ctx)
    assert res.status == "done" and res.summary == "Line one\nLine 2" and ctx.store.approvals() == []
    for real in ("print(2 + 2)", "import os\nprint('x')", "print(open('a.txt').read())", "x = 'a'\nprint(x)"):
        assert CodeAgent().run("compute", {"code": real}, ctx).status == "waiting_approval", real


def test_only_the_bare_title_bar_drags_the_window(tmp_home):
    """Owner (21 Nov): minimise / maximise / close never worked. pywebview started its own window move on a
    mouse-down anywhere inside the title bar, buttons included, so a click moved the maximised window instead of
    pressing the button. Only the bar itself (not what is on it) may start a drag."""
    from prometheus.auth import hash_password
    from prometheus.config import Settings
    from prometheus.desktop import run_desktop
    from test_v11_desktop import FakeServer, FakeWebview

    wv = FakeWebview()
    wv.settings = {"DRAG_REGION_SELECTOR": ".pywebview-drag-region", "DRAG_REGION_DIRECT_TARGET_ONLY": False}
    assert run_desktop(Settings(password_hash=hash_password("a long password!")), webview=wv, server=FakeServer()) == 0
    assert wv.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] is True


def test_photo_models_are_released_when_not_used(monkeypatch):
    """Owner (21 Nov): RAM at 100%, Prometheus's Python on top. The photo models (about 1 GB) loaded once and stayed
    for good. They now go after a short idle time and come back when needed."""
    import time

    from prometheus import photos

    loads = []

    def fake_load(self):
        if self._clip is None:
            loads.append(1)
            self._clip, self._labels, self._ocr = object(), object(), object()

    monkeypatch.setattr(photos.Looker, "_load", fake_load)
    monkeypatch.setattr(photos, "IDLE_SECONDS", 0.2)
    looker = photos.Looker()
    with looker._lock:
        looker._load()
    looker._used()
    assert looker._clip is not None
    time.sleep(0.6)
    assert looker._clip is None and looker._ocr is None and looker._labels is None
    with looker._lock:
        looker._load()
    assert len(loads) == 2


def test_the_local_voice_is_not_loaded_while_elevenlabs_speaks(app_client, monkeypatch):
    """With an ElevenLabs key the local voice (Kokoro, the biggest voice model) is only the fallback: it is loaded the
    first time the fallback is needed, not at start."""
    import prometheus.voice.audio as audio
    import prometheus.voice.engines as engines
    import prometheus.voice.loop as vloop

    _, services = app_client()
    made = []

    class Fake:
        def __init__(self, *a, **k):
            made.append(type(self).__name__)

    class KokoroSpeaker(Fake):
        interruptible = True

        def say(self, text, **k):
            made.append("said")

    class Transcriber(Fake):
        pass

    class WakeSpotter(Fake):
        pass

    class Microphone(Fake):
        pass

    class VoiceLoop(Fake):
        def start(self):
            pass

    for mod, name, cls in ((engines, "KokoroSpeaker", KokoroSpeaker), (engines, "Transcriber", Transcriber),
                           (engines, "WakeSpotter", WakeSpotter), (audio, "Microphone", Microphone),
                           (vloop, "VoiceLoop", VoiceLoop)):
        monkeypatch.setattr(mod, name, cls)
    voice = services.voice
    monkeypatch.setattr(voice.models, "packages_missing", lambda: [])
    monkeypatch.setattr(voice.models, "missing", lambda: [])
    monkeypatch.setattr(voice, "eleven_key", lambda: "key")
    monkeypatch.setattr(voice, "apply_profile", lambda p: None)
    services.settings.voice_clap = False
    assert voice.start() is True
    assert "KokoroSpeaker" not in made                               # not loaded while ElevenLabs does the talking
    from prometheus.voice.cloud import ElevenSpeaker

    assert isinstance(voice._speaker, ElevenSpeaker)                 # ElevenLabs talks; its fallback loads on first use
    lazy = voice._speaker.fallback
    lazy.say("hello")
    assert made.count("KokoroSpeaker") == 1 and "said" in made
    voice.loop = None
