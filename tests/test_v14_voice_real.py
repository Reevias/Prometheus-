"""The real voice models on spoken audio (made with the Piper voice). Opt-in: set PROMETHEUS_VOICE_MODELS to a folder
with the downloaded models. Skipped otherwise, so normal test runs need no audio packages or downloads."""
import os

import pytest

MODELS = os.environ.get("PROMETHEUS_VOICE_MODELS")
pytestmark = pytest.mark.skipif(not MODELS, reason="set PROMETHEUS_VOICE_MODELS to run the real voice models")


@pytest.fixture(scope="module")
def engines():
    import numpy as np
    from piper import PiperVoice

    from prometheus.voice.engines import Transcriber, VoiceModels, WakeSpotter

    models = VoiceModels(MODELS)
    assert models.missing() == []
    voice = PiperVoice.load(str(models.piper))

    def say(text):
        chunks = list(voice.synthesize(text))
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        n = int(len(audio) * 16000 / chunks[0].sample_rate)
        return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.int16)

    transcriber = Transcriber(models)
    return models, say, transcriber, lambda: WakeSpotter(models, transcriber)


def frames_of(*parts):
    import numpy as np

    silence = np.zeros(8000, np.int16)
    audio = np.concatenate([silence, *parts, silence, silence, silence]).tobytes()
    return [audio[i:i + 960] for i in range(0, len(audio) - 959, 960)]


def run_real(engines, *phrases):
    from tests.test_v14_voice import FakeMic, FakeSpeaker
    from prometheus.voice.loop import VoiceLoop

    models, say, transcriber, spotter = engines
    import numpy as np
    parts = []
    for p in phrases:
        parts += [say(p), np.zeros(4000, np.int16)]
    mic, speaker, asked = FakeMic(frames_of(*parts)), FakeSpeaker(), []
    loop = VoiceLoop(mic, spotter(), transcriber, speaker, lambda c: (asked.append(c), "Done.")[1])
    while True:
        frame = mic.read()
        if frame is None:
            break
        wake = loop.spotter.feed(frame)
        if wake is not None:
            loop.handle_wake(wake)
    return asked, speaker


def test_wakes_on_its_name_and_runs_the_command(engines):
    asked, speaker = run_real(engines, "Prometheus, open YouTube.")
    assert len(asked) == 1 and "youtube" in asked[0].lower() and speaker.said == ["Done."]


def test_hey_prometheus_works_too(engines):
    asked, _ = run_real(engines, "Hey Prometheus, what time is it?")
    assert len(asked) == 1 and "time" in asked[0].lower()


@pytest.mark.parametrize("talk", ["It was quite promising, and I promise to come.",
                                  "The problem with the process is that the professor promoted it.",
                                  "Please pass me the remote, it is on the table."])
def test_ordinary_talk_does_not_wake_it(engines, talk):
    asked, _ = run_real(engines, talk)
    assert asked == []


def test_name_then_pause_then_command(engines):
    import numpy as np

    models, say, transcriber, spotter = engines
    from tests.test_v14_voice import FakeMic, FakeSpeaker
    from prometheus.voice.loop import VoiceLoop

    mic = FakeMic(frames_of(say("Prometheus."), np.zeros(24000, np.int16), say("Play some lofi music.")))
    speaker, asked = FakeSpeaker(), []
    loop = VoiceLoop(mic, spotter(), transcriber, speaker, lambda c: (asked.append(c), "OK.")[1])
    while (frame := mic.read()) is not None:
        wake = loop.spotter.feed(frame)
        if wake is not None:
            loop.handle_wake(wake)
    assert speaker.chimes == 1 and len(asked) == 1 and "music" in asked[0].lower()
