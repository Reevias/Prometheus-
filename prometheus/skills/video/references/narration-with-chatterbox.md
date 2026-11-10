# Narration with Chatterbox

Source: https://github.com/resemble-ai/chatterbox (MIT, as of 2026-11-04)
Files read: README.md, pyproject.toml, src/chatterbox/tts_turbo.py (parts).

## Status warning

The README says that as of August 2026 Chatterbox is "retired research" and no longer maintained: no support or updates. The code stays public under MIT. Resemble AI says this repo is not a product of theirs. Expect no fixes.

## Models

| Model | Size | Languages | Notes |
|---|---|---|---|
| Chatterbox-Nano | 110M | English | Same design as Turbo; meant for on-device and CPU. README: about 3x faster than real time on 8 CPU cores |
| Chatterbox-Turbo | 350M | English | Lower compute; paralinguistic tags like `[laugh]`, `[chuckle]`, `[cough]` |
| Chatterbox-Multilingual V3 | 500M | 23+ | Voice cloning across languages; also six single-language packs |
| Original Chatterbox | 500M | English | `exaggeration` and `cfg_weight` tuning |

## Will it run on this laptop?

Likely only Nano, on CPU, and slowly. The Nano claim is for 8 cores; the owner's CPU is unknown. The Multilingual V3 and original models are meant for a GPU (the README example uses `cuda`, `cpu` or `mps`). Dependencies pin PyTorch 2.6 and need Python 3.10 or higher. The authors tested on Python 3.11 on Debian; Windows is not mentioned. Treat Windows use as untested.

## Install

1. Ask the owner's approval; this downloads PyTorch (large) and model weights from Hugging Face.
2. Use a fresh virtual environment (Python 3.11 suggested).
3. `pip install chatterbox-tts`, or clone the repo and `pip install -e .`.
4. The first call downloads the model.

## Basic use (from the README)

```python
import torchaudio as ta
from chatterbox.tts_turbo import ChatterboxTurboTTS
model = ChatterboxTurboTTS.from_pretrained(device="cpu", nano=True)
wav = model.generate(text, audio_prompt_path="reference.wav")
ta.save("out.wav", wav, model.sr)
```

Notes:
- The README example for Nano and Turbo passes a reference clip. The code asserts the clip is longer than 5 seconds. The code also looks for a built-in voice file (`conds.pt`) in the downloaded files, so a no-clip call may work; not verified.
- Multilingual: `ChatterboxMultilingualTTS.from_pretrained(device=..., t3_model="v3")` then `generate(text, language_id="fr")`.
- Keep each piece of text short; generate paragraph by paragraph and join the audio files.
- Example scripts in the repo: example_tts.py, example_tts_turbo.py, example_tts_nano.py, example_vc.py.

## Watermark

Every output carries an inaudible PerTh watermark meant to mark it as AI-made. Do not remove it.

## Consent rule for voice cloning

Voice cloning copies a person's voice from a short clip. Prometheus must follow this rule:

1. Use the owner's own voice, or a voice that the speaker has clearly agreed to be cloned (ideally in writing).
2. Never clone a celebrity, relative, teacher, colleague or anyone else without their consent.
3. Never use the output to pretend to be a real person, to deceive, scam or harass.
4. Say plainly that narration is AI-made when sharing it.
5. Keep reference clips and outputs in a private folder; do not upload them to cloud services.
6. The README's own disclaimer: do not use the model "to do bad things".

## Workflow for a narration job

1. Ask for the script text and the language.
2. Ask whose voice to use; apply the consent rule. If no clip is wanted, use the model default if it exists.
3. Test with one sentence; check speed and quality.
4. Generate in chunks; save WAV files in a project folder.
5. Report time taken and where files are.

## Not verified

- Nothing was run.
- Windows support and real CPU speed on the owner's laptop.
- Whether Nano works without a reference clip.
- Exact download sizes.
