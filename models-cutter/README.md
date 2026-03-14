# Model Cutter

Splits one monolithic `.safetensors` model into multiple files by tensor prefix and writes them next to the source model.

## Run In Docker

Enter running container:

```bash
docker exec -it comfyui bash
```

Then run cutter from inside container:

```bash
python3 "/workspace/models-cutter/split_safetensors_model.py" "/path/to/model.safetensors"
```

## What It Splits

If keys exist in the source model:

- `model.*` -> `split_files/diffusion_models/*_model.safetensors`
- `vae.*` -> `split_files/vae/*_video_vae.safetensors` (saved without `vae.` prefix for `Load VAE`)
- `audio_vae.*` and `vocoder.*` -> `split_files/audio_vae/*_audio_vae.safetensors`
- `text_embedding_projection.*` -> `split_files/text_encoders/*_text_projection.safetensors`

## Usage

Run from any folder:

```bash
python3 "/home/dr-vij/WorkProjects/ComfyDocker/models-cutter/split_safetensors_model.py" "/path/to/model.safetensors"
```

If you are already in the model folder:

```bash
python3 "/home/dr-vij/WorkProjects/ComfyDocker/models-cutter/split_safetensors_model.py" "./ltx-2.3-22b-distilled.safetensors"
```

Optional flags:

- `--out-dir /custom/output/folder` - custom output directory
- `--force` - overwrite existing split files

## Requirement

Python environment must have:

- `safetensors`
- `torch`

Example with Comfy venv Python:

```bash
/home/dr-vij/WorkProjects/ComfyDocker/venv/bin/python "/home/dr-vij/WorkProjects/ComfyDocker/models-cutter/split_safetensors_model.py" "./ltx-2.3-22b-distilled.safetensors"
```
