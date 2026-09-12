# Model cutter

Splits one monolithic `.safetensors` model into the separate files ComfyUI's
loaders expect, written next to the source model.

## Run

From inside the container, where `safetensors` and `torch` are already
installed:

```bash
docker exec -it comfyui python3 /workspace/models-cutter/split_safetensors_model.py \
  /workspace/ComfyUI/models/checkpoints/ltx-2.3-22b-distilled.safetensors
```

On the host, using the project venv:

```bash
./venv/bin/python models-cutter/split_safetensors_model.py \
  ../ComfyData/models/checkpoints/ltx-2.3-22b-distilled.safetensors
```

Options:

- `--out-dir <path>` writes elsewhere than beside the source file
- `--force` overwrites existing split files

## What it splits

Only prefixes present in the source produce a file.

| Source prefix | Written to |
| --- | --- |
| `model.*` | `split_files/diffusion_models/*_model.safetensors` |
| `vae.*` | `split_files/vae/*_video_vae.safetensors`, with the `vae.` prefix stripped so `Load VAE` accepts it |
| `audio_vae.*`, `vocoder.*` | `split_files/audio_vae/*_audio_vae.safetensors` |
| `text_embedding_projection.*` | `split_files/text_encoders/*_text_projection.safetensors` |
