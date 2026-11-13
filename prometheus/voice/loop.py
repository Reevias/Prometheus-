"""The listening loop: wait for "Prometheus", take the command, run it, speak the answer."""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

from prometheus.voice.audio import Endpointer, NoiseFloor, rms
from prometheus.voice.text import speakable, strip_wake

CARRY_ON = 0.7       # seconds of quiet after the name that mean "that was all, now ask for the command"
FOLLOW_UP = 6.0      # after an answer: seconds to just keep talking, without the name again
SESSION_IDLE = 15.0  # after a double clap: the conversation ends after this long with nobody talking
GOODBYE = re.compile(r"^(?:ok(?:ay)?\s+)?(?:that'?s (?:all|it)|goodbye|bye|thank you,? that'?s (?:all|it)|stop listening|"
                     r"nothing|no thanks?|never ?mind|go to sleep|dismissed)\b", re.I)
STATES = ("off", "setup", "idle", "listening", "thinking", "speaking", "muted", "error")


def _was_reader_command(command: str) -> bool:
    from prometheus.quick import READER_COMMAND

    return bool(READER_COMMAND.search(command or ""))


class VoiceLoop:
    """Runs in its own thread. Every part is passed in, so it can be tested without a microphone.

    mic: .start() .stop() .read(timeout) -> frame | None, .flush()
    spotter: .feed(frame) -> audio of the wake word or None, .reset()
    transcriber: .text(audio) -> str
    speaker: .say(text), .chime()
    on_command(text) -> the answer to speak ("" to stay quiet)
    """

    def __init__(self, mic, spotter, transcriber, speaker, on_command: Callable[[str], str],
                 log: Callable[[str], None] = lambda m: None, clap=None, on_clap: Callable[[], str] | None = None):
        self.mic, self.spotter, self.transcriber, self.speaker = mic, spotter, transcriber, speaker
        self.on_command = on_command
        self.log = log
        self.state = "idle"
        self.muted = False
        self.last_heard = ""
        self.last_timing: dict = {}      # seconds per stage of the last command, shown in Settings
        self.followup = None             # callable -> True when the answer asked something (a quiz question)
        self._announcing = 0             # other threads speaking (reminders, alerts): the mic is ignored meanwhile
        self.last_said, self.last_said_at = "", 0.0     # to recognise our own voice coming back through the mic
        self._announce_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.noise = NoiseFloor()
        self.clap = clap                 # ClapDetector, or None when the double clap is off
        self.on_clap = on_clap           # -> the greeting and brief to speak
        self.follow_up = 0.0             # seconds to keep talking after a "Prometheus" answer (0: single questions)
        self.barge_in = True             # talking over an answer stops it
        self._barged = b""               # what the owner said over the answer
        self.level = 0.0                 # how loud the mic is right now, 0..1 (the waveform and the sphere follow it)
        self._listen_now = threading.Event()     # the mic button: listen as if the wake word was said

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

    def listen_now(self) -> bool:
        """The mic button in the app: start listening for a command without the wake word. False when muted."""
        if self.muted:
            return False
        self._listen_now.set()
        return True

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.state = "muted" if muted else "idle"
        self.mic.flush()
        self.spotter.reset()

    # -- the loop --------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            if getattr(self, "training", False):      # the wake phrase is being trained: it reads the mic
                time.sleep(0.05)
                continue
            frame = self.mic.read(timeout=0.5)
            if frame is None:
                continue
            if self.muted or self._announcing:
                self.level = 0.0
                continue                       # never listen to our own voice
            self.level = min(1.0, rms(frame) / 4000.0)
            self.noise.update(frame)
            if self._listen_now.is_set():
                self._listen_now.clear()
                try:
                    self.handle_button()       # "Yes sir?", then the command
                except Exception as e:
                    self.log(f"voice turn failed: {type(e).__name__}: {e}")
                finally:
                    self.mic.flush()
                    self.spotter.reset()
                    if not self.muted:
                        self.state = "idle"
                continue
            if self.clap is not None and self.on_clap is not None and self.clap.feed(frame):
                try:
                    self.handle_clap()
                except Exception as e:
                    self.log(f"clap session failed: {type(e).__name__}: {e}")
                finally:
                    self.mic.flush()
                    self.spotter.reset()
                    self.clap.reset()
                    if not self.muted:
                        self.state = "idle"
                continue
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

    def handle_button(self) -> None:
        """The mic button: like saying only the wake word ("Yes sir?", then the command), but never reusing what
        the wake-word spotter heard earlier."""
        self.state = "listening"
        t0 = time.monotonic()
        self._speak_quietly(getattr(self.speaker, "ask", self.speaker.chime))
        verdict, audio = self._record()
        command = self.transcriber.text(audio).strip() if verdict == "done" else ""
        command = strip_wake(command) or command
        if not command:
            self.log("mic button: heard no command")
            return
        if self.is_own_echo(command):
            return
        self._answer(command, t0)

    def handle_wake(self, wake_audio: bytes) -> None:
        """After "Hello Prometheus": a command said in the same breath is used straight away (no second listen);
        the name on its own gets "Yes sir?" and then the command. Either way "Yes sir." confirms it was heard."""
        self.state = "listening"
        t0 = time.monotonic()
        from prometheus.voice import reader as _reader

        reading = _reader.CURRENT if (_reader.CURRENT is not None and _reader.CURRENT.state == "reading") else None
        if reading is not None:
            reading.pause()                    # quiet while the owner speaks
        heard = getattr(self.spotter, "last_text", None)
        phrase_done = getattr(self.spotter, "phrase_done", False)
        command = strip_wake(heard) if heard and phrase_done else ""
        if command and getattr(self.transcriber, "command", False):
            # The wake check used the fast model; the chosen (more accurate) model hears the command again.
            again = strip_wake(self.transcriber.text(wake_audio))
            command = again or command
        if not command and not (heard is not None and phrase_done):
            verdict, audio = self._record(lead=wake_audio, wait=CARRY_ON)     # still talking: hear it out
            command = strip_wake(self.transcriber.text(audio))
        if not command:                                # just the name: ask for the command
            self._speak_quietly(getattr(self.speaker, "ask", self.speaker.chime))
            verdict, audio = self._record()
            command = self.transcriber.text(audio).strip() if verdict == "done" else ""
            command = strip_wake(command) or command   # "Prometheus, ..." said again is fine too
        if not command:
            self.log("woke up but heard no command")
            return
        if self.is_own_echo(command):
            self.log("ignored: that was my own voice coming back")
            return
        if self.unclear() and getattr(self.transcriber, "command", False):
            self._speak_quietly(getattr(self.speaker, "ask", self.speaker.chime))   # "Yes sir?": say it again
            verdict, audio = self._record()
            again = self.transcriber.text(audio).strip() if verdict == "done" else ""
            if not again or self.unclear():
                self._say("Sorry sir, I didn't catch that.")
                return
            command = strip_wake(again) or again
        self._answer(command, t0)
        if not (_reader.CURRENT is not None and _reader.CURRENT.state == "reading"):
            self.converse(self.follow_up, after_wake=True)      # not while reading aloud: it would hear the reading
        if reading is not None and reading.state == "paused" and not _was_reader_command(command):
            reading.start()                    # another question answered: carry on reading

    def _answer(self, command: str, t0: float | None = None) -> bool:
        """Run one command and speak the answer. True when the owner talked over the answer."""
        t0 = t0 if t0 is not None else time.monotonic()
        self.last_heard = command
        t_heard = time.monotonic()
        from prometheus.quick import is_question

        ack = getattr(self.speaker, "ack", None)
        if not is_question(command):
            # an action: say what was heard, so a mishearing is caught before anything happens
            self._say(f"Yes sir. {command.rstrip('.!?')}.")
        elif ack is not None:
            self._speak_quietly(ack)                   # "Yes sir." — you know it heard you
        self.state = "thinking"
        t_think = time.monotonic()
        answer = self.on_command(command)
        t_answer = time.monotonic()
        cut_in = False
        if answer:
            self.state = "speaking"
            cut_in = self._say(answer, limit=320, barge=True)
        self.last_timing = {"hearing": round(t_heard - t0, 1), "thinking": round(t_answer - t_think, 1),
                            "speaking": round(time.monotonic() - t_answer, 1)}
        self.log(f"voice timing: {self.last_timing}")
        self._quiz()
        return cut_in

    def _quiz(self) -> None:
        # a quiz question was asked: the owner's answer needs no "Hello Prometheus"
        turns = 0
        while self.followup is not None and self.followup() and turns < 40 and not self._stop.is_set():
            turns += 1
            self.state = "listening"
            verdict, audio = self._record(wait=8.0)
            reply = self.transcriber.text(audio).strip() if verdict == "done" else ""
            if self.is_own_echo(reply):
                continue                          # that was the question coming back from the speakers
            if not reply:
                reply = "stop"                    # silence ends the quiz
            self.last_heard = reply
            self.state = "thinking"
            answer = self.on_command(reply)
            if answer:
                self.state = "speaking"
                self._say(answer, limit=400)

    def handle_clap(self) -> None:
        """Two claps: greet the owner, give the brief, then keep listening so they can just talk."""
        self.state = "thinking"
        self.log("double clap heard")
        try:
            brief = self.on_clap() or "Yes sir?"
        except Exception as e:
            self.log(f"brief failed: {type(e).__name__}: {e}")
            brief = "Hello sir. What can I do for you?"
        self.state = "speaking"
        self._say(brief, limit=900, barge=True)
        self.converse(SESSION_IDLE)

    def converse(self, idle: float, after_wake: bool = False) -> None:
        """Keep the conversation going without the name: each thing said is answered, until nobody talks for
        `idle` seconds or the owner says "that's all". Talking over an answer counts as the next thing said."""
        if idle <= 0:
            self._barged = b""
            return
        turns = 0
        while turns < 30 and not self._stop.is_set() and not self.muted:
            turns += 1
            self.state = "listening"
            lead, self._barged = self._barged, b""
            if lead:
                verdict, audio = self._record(lead=lead, wait=CARRY_ON * 2)
                verdict = "done"
            else:
                verdict, audio = self._record(wait=idle)
            if verdict != "done":
                if not after_wake:
                    self._say("I'll be here, sir.")
                return
            said = self.transcriber.text(audio).strip()
            said = strip_wake(said) or said
            if not said or self.is_own_echo(said):
                continue
            if self.unclear() and getattr(self.transcriber, "command", False):
                if after_wake:
                    return                         # probably not meant for me: go back to waiting quietly
                self._say("Sorry sir, I didn't catch that.")
                continue
            if GOODBYE.match(said):
                self._say("Alright sir.")
                return
            self._answer(said)

    def announce(self, text: str) -> None:
        """Speak from another thread (a reminder, an alert, the briefing) with the microphone ignored, and with
        anything that sounds like the wake name taken out of text that came from outside."""
        from prometheus.voice.text import scrub_wake

        with self._announce_lock:
            self._announcing += 1
        try:
            self.last_said, self.last_said_at = scrub_wake(text), time.monotonic()
            self.speaker.say(self.last_said)
        finally:
            time.sleep(0.3)
            self.mic.flush()
            self.spotter.reset()
            with self._announce_lock:
                self._announcing -= 1

    def _say(self, text: str, limit: int | None = None, barge: bool = False) -> bool:
        """Speak with the microphone ignored. With barge=True the owner can talk over it: it stops, and what they
        said is kept for the next turn. Returns True when that happened."""
        from prometheus.voice.text import scrub_wake

        line = scrub_wake(speakable(text, limit=limit) if limit else text)
        self.last_said, self.last_said_at = line, time.monotonic()
        if not (barge and self.barge_in and getattr(self.speaker, "interruptible", False)):
            self._speak_quietly(lambda: self.speaker.say(line))
            return False
        from prometheus.voice.bargein import BargeIn

        stop = threading.Event()
        watch = BargeIn(self.mic, self.noise.level, stop, on_cut=self.speaker.stop)
        watcher = threading.Thread(target=watch.run, name="prometheus-barge-in", daemon=True)
        with self._announce_lock:
            self._announcing += 1
        try:
            watcher.start()
            self.speaker.say(line, stop=stop)
        finally:
            watch.done.set()
            watcher.join(timeout=2)
            with self._announce_lock:
                self._announcing -= 1
        if watch.cut:
            self.log("the owner talked over the answer: stopped speaking")
            self._barged = watch.audio()
            return True
        time.sleep(0.6)
        self.mic.flush()
        return False

    def is_own_echo(self, heard: str) -> bool:
        """True when what the mic picked up is just Prometheus's own last words coming back from the speakers."""
        import difflib

        if not self.last_said or time.monotonic() - self.last_said_at > 45 or not heard:
            return False
        a = " ".join(heard.lower().split())
        b = " ".join(self.last_said.lower().split())
        if len(a) < 8:
            return False
        return a in b or difflib.SequenceMatcher(None, a, b).ratio() > 0.55 or \
            difflib.SequenceMatcher(None, a, b[:len(a) + 20]).ratio() > 0.6

    def unclear(self) -> bool:
        """Whisper was guessing (a noisy room, mumbling, or nothing said): better to ask again than to act."""
        conf = getattr(self.transcriber, "last_confidence", 0.0)
        no_speech = getattr(self.transcriber, "last_no_speech", 0.0)
        return conf < -1.15 or no_speech > 0.75

    def _speak_quietly(self, action) -> None:
        """Speak with the microphone ignored, so Prometheus never hears (and answers) itself."""
        with self._announce_lock:
            self._announcing += 1
        try:
            action()
        finally:
            time.sleep(0.6)                      # the room's echo dies down before listening again
            self.mic.flush()
            with self._announce_lock:
                self._announcing -= 1
