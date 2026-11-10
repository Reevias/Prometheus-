"""The listening loop: wait for "Prometheus", take the command, run it, speak the answer."""
from __future__ import annotations

import threading
import time
from typing import Callable

from prometheus.voice.audio import Endpointer, NoiseFloor
from prometheus.voice.text import speakable, strip_wake

CARRY_ON = 0.7       # seconds of quiet after the name that mean "that was all, now ask for the command"
STATES = ("off", "setup", "idle", "listening", "thinking", "speaking", "muted", "error")


class VoiceLoop:
    """Runs in its own thread. Every part is passed in, so it can be tested without a microphone.

    mic: .start() .stop() .read(timeout) -> frame | None, .flush()
    spotter: .feed(frame) -> audio of the wake word or None, .reset()
    transcriber: .text(audio) -> str
    speaker: .say(text), .chime()
    on_command(text) -> the answer to speak ("" to stay quiet)
    """

    def __init__(self, mic, spotter, transcriber, speaker, on_command: Callable[[str], str],
                 log: Callable[[str], None] = lambda m: None):
        self.mic, self.spotter, self.transcriber, self.speaker = mic, spotter, transcriber, speaker
        self.on_command = on_command
        self.log = log
        self.state = "idle"
        self.muted = False
        self.last_heard = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.noise = NoiseFloor()

    # -- control ---------------------------------------------------------------------
    def start(self) -> None:
        self.mic.start()
        self._thread = threading.Thread(target=self.run, name="prometheus-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self.mic.stop()
        except Exception:
            pass
        self.state = "off"

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.state = "muted" if muted else "idle"
        self.mic.flush()
        self.spotter.reset()

    # -- the loop --------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            frame = self.mic.read(timeout=0.5)
            if frame is None:
                continue
            if self.muted:
                continue
            self.noise.update(frame)
            wake_audio = self.spotter.feed(frame)
            if wake_audio is not None:
                try:
                    self.handle_wake(wake_audio)
                except Exception as e:     # one bad turn never stops listening
                    self.log(f"voice turn failed: {type(e).__name__}: {e}")
                finally:
                    self.mic.flush()
                    self.spotter.reset()
                    if not self.muted:
                        self.state = "idle"

    def _record(self, lead: bytes = b"", wait: float = 5.0) -> tuple[str, bytes]:
        ep = Endpointer(noise_floor=self.noise.level, wait_seconds=wait)
        silent_reads = 0
        while not self._stop.is_set():
            frame = self.mic.read(timeout=0.5)
            if frame is None:
                silent_reads += 1
                if silent_reads >= 6:          # the microphone stopped sending audio: don't wait forever
                    return ("done" if ep.heard else "nothing"), lead + ep.audio()
                continue
            verdict = ep.feed(frame)
            if verdict != "more":
                return verdict, lead + ep.audio()
        return "nothing", lead

    def handle_wake(self, wake_audio: bytes) -> None:
        """After the name: if you keep talking, the whole sentence is written down in one go (so no word at the edge
        is lost); if you stop, Prometheus checks whether the command was already said, else chimes and listens."""
        self.state = "listening"
        verdict, audio = self._record(lead=wake_audio, wait=CARRY_ON)
        command = strip_wake(self.transcriber.text(audio))
        if not command:                                # just "Prometheus": ask for the command
            self._speak_quietly(self.speaker.chime)
            verdict, audio = self._record()
            command = self.transcriber.text(audio).strip() if verdict == "done" else ""
        if not command:
            self.log("woke up but heard no command")
            return
        self.last_heard = command
        self.state = "thinking"
        answer = self.on_command(command)
        if answer:
            self.state = "speaking"
            self._speak_quietly(lambda: self.speaker.say(speakable(answer)))

    def _speak_quietly(self, action) -> None:
        """Speak with the microphone ignored, so Prometheus never hears (and answers) itself."""
        action()
        time.sleep(0.2)
        self.mic.flush()
