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
                "speech": self.models.speech}

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
                self.error = 'Voice needs extra packages. Run:  pip install -e ".[voice]"'
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

                transcriber = Transcriber(self.models)
                command_tr = (Transcriber(self.models, command=True)
                              if self.models.command_whisper != self.models.whisper else transcriber)
                loop = VoiceLoop(Microphone(), WakeSpotter(self.models, transcriber), command_tr,
                                 KokoroSpeaker(self.models, self.services.settings.voice_name), self.command,
                                 log=lambda m: self.services.store.audit("voice", "note", {"text": m}))
                loop.start()
                self.loop = loop
                self.apply_profile(self.profile)
            except Exception as e:      # no microphone, broken driver, damaged model…
                self.error = f"Voice could not start: {type(e).__name__}: {e}"
                return False
            self.loop, self.error = loop, ""
            return True

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
