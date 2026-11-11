"""Connects the listening loop to Prometheus: spoken commands go to a "Voice" chat and the answer is spoken back."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from prometheus.voice.engines import VoiceModels

ANSWER_WAIT = 300          # seconds to wait for a spoken command's answer


class VoiceService:
    def __init__(self, services, folder: Path):
        self.services = services
        self.models = VoiceModels(folder, getattr(services.settings, "voice_speech", "fast"))
        self.loop = None
        self.progress = ""
        self.downloading = False
        self.error = ""
        self._lock = threading.Lock()
        from prometheus.voice.training import ProfileStore, Trainer

        self.profiles = ProfileStore(folder)
        self.trainer = Trainer(self)
        self.profile = self.profiles.load()
        from prometheus.voice.text import set_learned

        set_learned(self.profile)

    def apply_profile(self, profile: dict | None) -> None:
        """Use (or, with None, stop using) what training learned about the owner's voice."""
        from prometheus.voice.text import set_learned
        from prometheus.voice.training import thresholds

        self.profile = profile
        set_learned(profile)
        spotter = getattr(self.loop, "spotter", None)
        if spotter is not None:
            t = thresholds(profile)
            from prometheus.voice.engines import BURST_MAX

            spotter.loud_min = t.get("loud", 500.0)
            spotter.burst_max = t.get("burst_max", BURST_MAX)

    # -- what the screen shows ----------------------------------------------------------
    def state(self) -> str:
        if self.loop is not None:
            return self.loop.state
        if self.downloading:
            return "setup"
        if self.error:
            return "error"
        return "off"

    def describe(self) -> dict:
        return {"state": self.state(), "progress": self.progress, "error": self.error,
                "missing": self.models.missing(), "packages": self.models.packages_missing(),
                "heard": getattr(self.loop, "last_heard", "") if self.loop else "",
                "timing": getattr(self.loop, "last_timing", {}) if self.loop else {},
                "speech": self.models.speech, "engine": self.engine(),
                "heard_by": getattr(getattr(self.loop, "transcriber", None), "last_engine", "") if self.loop else ""}

    # -- starting and stopping ------------------------------------------------------------
    def start_if_enabled(self) -> None:
        if self.services.settings.voice_enabled:
            threading.Thread(target=self.start, daemon=True).start()

    def start(self) -> bool:
        with self._lock:
            if self.loop is not None:
                return True
            self.models.speech = getattr(self.services.settings, "voice_speech", self.models.speech)
            packages = self.models.packages_missing()
            if packages:
                self.error = 'Voice needs extra packages. To fix it, run the installer again (irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex)'
                return False
            if self.models.missing():
                if not self.downloading:                 # e.g. the new voice after an update: fetch it by itself
                    self.download()
                self.error = "Getting the voice ready (downloading)…"
                return False
            try:
                from prometheus.voice.audio import Microphone
                from prometheus.voice.engines import KokoroSpeaker, Transcriber, WakeSpotter
                from prometheus.voice.loop import VoiceLoop

                s = self.services.settings
                transcriber = Transcriber(self.models)
                command_tr = (Transcriber(self.models, command=True)
                              if self.models.command_whisper != self.models.whisper else transcriber)
                speaker = KokoroSpeaker(self.models, s.voice_name)
                key = self.eleven_key()
                if key:
                    # the chatbot-level voice; the local models stay ready as the fallback
                    from prometheus.voice.cloud import ElevenHearing, ElevenSpeaker

                    command_tr = ElevenHearing(key, fallback=command_tr)
                    speaker = ElevenSpeaker(key, getattr(s, "eleven_voice", ""), fallback=speaker)
                clap = None
                if getattr(s, "voice_clap", True):
                    from prometheus.voice.clap import ClapDetector

                    clap = ClapDetector(getattr(s, "clap_sensitivity", 1.0))
                loop = VoiceLoop(Microphone(), WakeSpotter(self.models, transcriber), command_tr, speaker,
                                 self.command, log=lambda m: self.services.store.audit("voice", "note", {"text": m}),
                                 clap=clap, on_clap=self.clap_brief)
                loop.barge_in = getattr(s, "voice_barge_in", True)
                loop.followup = self.quiz_running
                loop.start()
                self.loop = loop
                self.apply_profile(self.profile)
            except Exception as e:      # no microphone, broken driver, damaged model…
                self.error = f"Voice could not start: {type(e).__name__}: {e}"
                return False
            self.loop, self.error = loop, ""
            return True

    def eleven_key(self) -> str:
        """The ElevenLabs key when that voice is chosen (auto: whenever the key is in)."""
        engine = getattr(self.services.settings, "voice_engine", "auto")
        if engine == "local":
            return ""
        from prometheus.voice.cloud import api_key

        return api_key()

    def engine(self) -> str:
        if self.loop is not None:
            return "elevenlabs" if type(self.loop.speaker).__name__ == "ElevenSpeaker" else "local"
        return "elevenlabs" if self.eleven_key() else "local"

    def clap_brief(self) -> str:
        from prometheus.voice.brief import build

        text = build(self.services)
        try:
            self.services.store.add_message(self.voice_chat(), "assistant", text, "voice brief", private=True)
        except Exception:
            pass
        return text

    def restart(self, wait: bool = False) -> None:
        """Start listening again with new settings. In the background by default: loading the speech models takes
        a while, and the Settings page shouldn't hang meanwhile."""
        if self.loop is None:
            return

        def again():
            try:
                self.stop()
                self.start()
            except Exception as e:
                self.error = f"Voice could not restart: {type(e).__name__}: {e}"
        if wait:
            again()
        else:
            threading.Thread(target=again, name="prometheus-voice-restart", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            if self.loop is not None:
                self.loop.stop()
                self.loop = None

    def toggle_mute(self) -> bool:
        if self.loop is None:
            return False
        self.loop.set_muted(not self.loop.muted)
        return self.loop.muted

    # -- setup -----------------------------------------------------------------------------
    def download(self) -> None:
        if self.downloading:
            return

        def work():
            self.downloading, self.error = True, ""
            try:
                self.models.download(progress=lambda text: setattr(self, "progress", text))
            except Exception as e:
                self.error = f"Download failed: {type(e).__name__}: {e}"
            finally:
                self.downloading = False
            if not self.error and self.services.settings.voice_enabled:
                self.start()

        threading.Thread(target=work, daemon=True).start()

    # -- a spoken command -----------------------------------------------------------------------
    def quiz_running(self) -> bool:
        from prometheus.agents import study_agent

        try:
            return study_agent.STUDY is not None and study_agent.STUDY.quiz_state(self.voice_chat()) is not None
        except Exception:
            return False

    def voice_chat(self) -> int:
        store = self.services.store
        title = "Voice " + time.strftime("%Y-%m-%d")
        for chat_id, name in store.chats():
            if name == title:
                return chat_id
        chat_id = store.create_chat(title)
        store.rename_chat(chat_id, title, by="user")     # automatic naming leaves it alone
        return chat_id

    def command(self, text: str) -> str:
        """Run a spoken command like a typed one and return the answer to speak."""
        store = self.services.store
        chat_id = self.voice_chat()
        if chat_id in self.services.running:
            return "I'm still working on the last thing you asked."
        before = len(store.messages(chat_id))
        store.audit("voice", "command", {"chars": len(text)})
        thread = self.services.start_task(chat_id, text)
        thread.join(timeout=ANSWER_WAIT)
        if thread.is_alive():
            return "This is taking a while. I'll put the answer in the Voice chat."
        new = [m for m in store.messages(chat_id)[before:] if m.role == "assistant"]
        return new[-1].text if new else ""
