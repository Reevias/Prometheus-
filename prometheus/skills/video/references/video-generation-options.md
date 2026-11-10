# Video generation options

Sources:
- https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5 (Tencent Hunyuan Community License, as of 2026-04-10). Files read: README.md, LICENSE.
- https://github.com/anil-matcha/open-generative-ai (MIT, as of 2026-11-09). File read: README.md.

## Short answer for this laptop

Windows 11, 8 GB RAM, no GPU cannot generate video locally with these tools. HunyuanVideo-1.5 needs an NVIDIA GPU with CUDA; Open Generative AI runs its local video engine only with a CUDA or ROCm GPU, otherwise it calls a paid cloud service.

## HunyuanVideo-1.5

- 8.3 billion parameter text-to-video and image-to-video model; 480p and 720p, with super-resolution to 1080p.
- Requirements (README): NVIDIA GPU with CUDA; minimum 14 GB GPU memory with model offloading on; Linux; Python 3.10 or higher. Install with `pip install -r requirements.txt`. Weights are downloaded from Hugging Face (see checkpoints-download.md).
- Run: `generate.py` with `--prompt`, `--resolution 480p|720p`, `--model_path`, optional `--image_path` for image-to-video, `--video_length` (default 121 frames), `--num_inference_steps` (default 50). `--enable_step_distill` is recommended for the 480p image-to-video model (8 or 12 steps).
- Prompt rewriting expects a large vLLM-served model; without an endpoint it runs without rewriting.
- Low-VRAM route named in the README: Wan2GP (https://github.com/deepbeepmeep/Wan2GP), said to work from about 6 GB of VRAM for this model. It still needs an NVIDIA/CUDA-class GPU. It does not rescue a laptop with no GPU.
- Other routes: Diffusers and ComfyUI integrations.

## Licence limits

- The Tencent Hunyuan Community License says it does not apply in the European Union, United Kingdom or South Korea; the licensed "Territory" is the world excluding those. Use, copying and showing of the model or its output outside the Territory is unlicensed.
- You may not use the model or its output to improve another AI model.
- A user count above 100 million monthly active users needs a separate licence from Tencent.
- An Acceptable Use Policy applies. Tencent claims no rights in your outputs.
- Practical rule: ask the owner where they are and where the output will be used before using or sharing anything. If they are in an excluded place, do not use it. This is not legal advice.

## Free cloud GPU route

A free notebook service with a GPU (for example Google Colab) may give a few hours of a GPU per day. The repos read do not document this. If the owner tries it: use a throwaway notebook, upload no personal files, check the notebook's GPU is large enough (14 GB or more for Hunyuan, about 8 GB for the small Wan 2.1 1.3B model named in the Open Generative AI README), and check the licence territory. Note that session limits, queue and storage change often.

## Open Generative AI and MuAPI

- A studio app (images, video, audio, lip sync) with desktop installers for Windows, macOS and Linux (Electron), plus a hosted site.
- It relies on the paid MuAPI service for 600+ cloud models. You paste a MuAPI access key into the app; keys are kept in browser localStorage and sent only to MuAPI (README claim). Costs are per use on MuAPI; prices not stated in the README.
- Local mode (desktop only): bundled stable-diffusion.cpp for images (runs on CPU, so slow, image-only), and optional Wan2GP for video, needing a CUDA or ROCm GPU.
- The README says it has no content filters. Prometheus still refuses harmful, deceptive or non-consensual media.
- Windows installer is not code-signed; SmartScreen will warn. Verify the source first.

## Choosing

| Situation | Suggest |
|---|---|
| Owner wants a short clip, no GPU, small budget | Paid API with a low spend limit, or a free cloud GPU session |
| Wants to learn how it works | Read the README; no local run |
| Wants private generation | Needs a GPU machine; not this laptop |
| In EU, UK, South Korea | Not Hunyuan |
| Wants narration only | See narration-with-chatterbox.md |

## Steps

1. Ask what the clip is for and its length.
2. Check licence territory and any real people or faces involved.
3. Pick a route from the table; state the cost or limit.
4. Get approval before installing or paying.
5. Keep prompts free of personal details when using cloud services.

## Not verified

- Nothing was run.
- Free cloud GPU details are general knowledge, not from the repos.
- MuAPI prices and terms were not read.
- Whether Wan2GP truly runs on 6 GB (README claim).
