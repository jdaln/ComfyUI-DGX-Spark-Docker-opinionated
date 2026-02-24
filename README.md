# ComfyDocker

Docker container for running [ComfyUI](https://github.com/Comfy-Org/ComfyUI) with NVIDIA CUDA 13.0 and GPU acceleration.

## About

**Disclaimer:** this is a strongly opinionated setup for my personal DGX Spark. Do whatever you want with it — I am sharing because DGX Spark is a new platform and I keep hunting for details myself.

This project provides a ready-to-use Docker infrastructure for running ComfyUI — a powerful node-based interface for Stable Diffusion and other generative models.

**This Docker setup was specifically designed for NVIDIA DGX Spark and ComfyUI.**

### Features

- **CUDA 13.0** — base image `nvcr.io/nvidia/cuda:13.0.2-devel-ubuntu24.04`
- **Python 3.12** — modern Python version from Ubuntu 24.04
- **onnxruntime-gpu** — built from source for CUDA 13.0 compatibility
- **Automatic dependency management** — installation and updates on each startup
- **Custom nodes support** — automatic cloning and updating from a list
- **Persistent storage** — models, caches, and data are preserved between restarts
- **Custom wheels** — self-built wheels are used for CUDA 13.0 compatibility and stored for reuse

### What is special here

The most important part is the custom wheels built inside the Docker image. Currently:

- **flash-attn3** — installed from a pre-built wheel inside the image
- **onnxruntime-gpu** — built for CUDA 13.0

Plan: rebuild **flash-attn4** in the future. If you do not want to rebuild, you can use the ready wheels from `SelfBuiltWheels/` (the container backs up built wheels there).

## Quick Start

### 1. Clone the repository

```bash
git clone --recursive https://github.com/dr-vij/DomfyUI-DGX-Spark-Docker-opinionated.git
cd ComfyDocker
```

If the repository was already cloned without `--recursive`:

```bash
git submodule update --init --recursive
```

### 2. Create .env file

Create a `.env` file in the project root:

```dotenv
UID=1000
GID=1000
UPDATE_DEPS=true
```

**Parameters:**
- `UID` — user ID (find with: `id -u`)
- `GID` — group ID (find with: `id -g`)
- `UPDATE_DEPS` — update ComfyUI and custom nodes on each startup (`true`/`false`)
- `COMFY_PORT` — web interface port (default: `8188`)

Note: `UPDATE_DEPS=true` forces dependency updates inside the container, but it does **not** update this repo. Pull the repo separately with `git pull`.

### 3. Create data directory

```bash
mkdir -p ../ComfyData/{models,user,input,output}
```

### 4. Build Docker image

```bash
docker compose build
```

> ⚠️ **Note:** The first build may take a significant amount of time (30+ minutes) as onnxruntime-gpu is compiled from source. Pre-built wheels for DGX Spark are planned for the future to speed up this process.

### 5. Start

```bash
docker compose up
```

After startup, ComfyUI will be available at: **http://localhost:8188**

### Stop

```bash
docker compose down
```

## Custom Nodes Management

### Adding custom nodes

Edit the `custom_nodes/custom_nodes.txt` file, adding the git repository URL:

```
https://github.com/author/ComfyUI-CustomNode.git
```

On the next container startup, the node will be automatically cloned and its dependencies installed.

### Pre-installed custom nodes

The default configuration includes:

- **ComfyUI-Manager** — GUI for managing custom nodes
- **ComfyUI-AdvancedLivePortrait** — advanced Live Portrait
- **ComfyUI-Chord** — by Ubisoft
- **ComfyUI-GGUF** — GGUF models support
- **ComfyUI-Inspire-Pack** — useful nodes collection
- **ComfyUI-KJNodes** — additional nodes by kijai
- **ComfyUI-segment-anything-2** — SAM2 segmentation
- **ComfyUI_IPAdapter_plus** — IP-Adapter
- **comfyui_controlnet_aux** — ControlNet preprocessors
- And more...

### Disabling custom nodes

Comment out the line in `custom_nodes.txt` with `#`:

```
# https://github.com/author/ComfyUI-CustomNode.git
```

### What is in `custom_nodes` and how to add/remove

`custom_nodes/` mounts a directory with `custom_nodes.txt`; the repo list is read at container startup.  
Add: put a repo URL into `custom_nodes/custom_nodes.txt`.  
Remove: delete the line or comment it with `#`, then the repo will not be pulled on next start.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UID` | User ID for running the container | — |
| `GID` | Group ID for running the container | — |
| `UPDATE_DEPS` | Update ComfyUI and custom nodes | `false` |
| `COMFY_PORT` | Web interface port | `8188` |

### Volumes

| Container Path | Local Path | Description |
|----------------|------------|-------------|
| `/workspace/venv` | `./venv` | Python virtual environment |
| `/workspace/pip_cache` | `./pip_cache` | pip cache |
| `/workspace/cache/huggingface` | `./hf_cache` | Hugging Face cache |
| `/workspace/cache/ultralytics` | `./ultralytics_cache` | YOLO cache |
| `/workspace/ComfyUI` | `./ComfyUI` | ComfyUI source code |
| `/workspace/ComfyUI/custom_nodes` | `./custom_nodes` | Custom nodes |
| `/workspace/ComfyUI/models` | `../ComfyData/models` | Models |
| `/workspace/ComfyUI/user` | `../ComfyData/user` | User data |
| `/workspace/ComfyUI/input` | `../ComfyData/input` | Input files |
| `/workspace/ComfyUI/output` | `../ComfyData/output` | Output files |

### Mounting folders one level above the repo

The `../ComfyData/*` folders are mounted one level above the repo so user data (models, input/output, etc.) does not live in git and is not lost to accidental cleanup.

## Adding Models

Place models in the appropriate subdirectories of `../ComfyData/models/`:

```
ComfyData/models/
├── checkpoints/      # Main models (SD 1.5, SDXL, Flux, etc.)
├── loras/            # LoRA models
├── controlnet/       # ControlNet models
├── vae/              # VAE models
├── embeddings/       # Text embeddings
├── upscale_models/   # Upscale models
└── ...
```

### Clearing caches

```bash
# Clear pip cache
rm -rf pip_cache/*

# Clear HuggingFace cache
rm -rf hf_cache/*

# Full venv reinstall
rm -rf venv/*
```

### How to wipe the environment

Minimal:

```bash
docker compose down
rm -rf venv/*
rm -rf pip_cache/*
```

Fully (including downloaded models and user data):

```bash
rm -rf ../ComfyData/*
```

### PyTorch version pin in entrypoint

In `entrypoint.sh`, PyTorch is always installed from the CUDA 13.0 pre-release index:

```bash
pip install torch torchvision torchaudio --pre --index-url https://download.pytorch.org/whl/cu130
```

This is an intentional pin for DGX Spark; change the line in `entrypoint.sh` if you want a different version flow.

## Changelog

### 2026-02-23 — decord support for ComfyUI-RMBG / SAM3

- Built **decord** wheel from [source](https://github.com/dr-vij/decord) (forked with FFmpeg 7 fixes) with CUDA/NVDEC enabled — no pre-built wheel exists for Python 3.12
- Added decord wheel to `entrypoint.sh` (backup + install on startup, same as flash-attn3 and onnxruntime-gpu)
- Enabled `video` driver capability in `docker-compose.yml` so the container gets access to hardware video decoding
- Added `libopengl0` to fix a missing library warning

## License

This repo only provides configuration and Docker wiring (and I do not really care about that). ComfyUI has its own license — see [ComfyUI License](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE). All models have their own licenses. All wheels and third-party libraries have their own licenses as well — please check and comply with each upstream.

## Author

dr-vij
