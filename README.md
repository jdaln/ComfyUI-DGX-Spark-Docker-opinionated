# ComfyUI-DGX-Spark-Docker-opinionated

ComfyUI in Docker for the NVIDIA DGX Spark. CUDA 13.0, Python 3.12, arm64.

This is a personal setup with opinions baked in. Take what is useful and change
the rest. If you like it, PRs are very welcome. Forked from this original work:
[dr-vij/ComfyUI-DGX-Spark-Docker-opinionated](https://github.com/dr-vij/ComfyUI-DGX-Spark-Docker-opinionated).

## What you get

- **GPU wheels for aarch64, built ahead of time.** flash-attn, flash-attn 3,
  onnxruntime-gpu, SageAttention and decord. No upstream release covers CUDA
  13.0 on Python 3.12 and arm64, so the repo builds and commits its own rather
  than compiling them on every machine.
- **27 custom node packs**, cloned at startup from
  [`custom_nodes/custom_nodes.txt`](custom_nodes/custom_nodes.txt).
- **53 asset profiles.** Name one in `.env` and the container downloads every
  model that workflow needs before it starts.
- **20 bundled templates** for models ComfyUI supports but ships no template
  for: Krea 2, Ideogram 4, Mage-Flow, MiniMax H3, HeartMuLa, VibeVoice and the
  LTX-2.3 task LoRAs.

## Requirements

| | |
| --- | --- |
| Host | NVIDIA DGX Spark, or another aarch64 machine with a Blackwell GPU |
| Software | Docker with the NVIDIA container runtime, `git`, `git-lfs` |
| Disk, fixed | ~25 GB for the image, the Python venv and the pip cache |
| Disk, models | 12–30 GB per image profile, 60–75 GB per video profile |

Install `git-lfs` before cloning. The prebuilt wheels are stored in LFS; without
it you get pointer files, and the image build falls back to compiling
onnxruntime and flash-attn from source.

## Install

### 1. Clone

```bash
git clone --recursive https://github.com/jdaln/ComfyUI-DGX-Spark-Docker-opinionated.git
cd ComfyUI-DGX-Spark-Docker-opinionated
```

Already cloned without `--recursive`:

```bash
git submodule update --init --recursive
```

### 2. Create the data directory

Models, inputs and outputs live one level above the repo so `git clean` cannot
touch them.

```bash
mkdir -p ../ComfyData/{models,user,input,output}
```

### 3. Configure

```bash
cp .env.example .env
```

Set `UID` and `GID` to your own (`id -u`, `id -g`). Everything else has a
working default. See [docs/configuration.md](docs/configuration.md) for the full
variable list.

Pick an asset profile now, so its models download during the first start instead
of needing a second one. [docs/workflows.md](docs/workflows.md) lists them all;
this one is a good starting point at 19 GB and about 20 seconds a render:

```dotenv
COMFY_ASSET_PROFILES=z-image-turbo-core
```

### 4. Build

```bash
docker compose build
```

### 5. Start

```bash
docker compose up -d
docker logs -f comfyui
```

## First start

The first run installs PyTorch and every custom node's dependencies into the
mounted venv, then downloads the models for the profiles you selected. ComfyUI's
HTTP server starts only after all of that finishes, so the port stays closed for
a while and the healthcheck allows 30 minutes before it reports unhealthy. This
is expected. Watch `docker logs -f comfyui` rather than the port.

Once the log shows the server listening, open <http://localhost:8188>.

Stop with `docker compose down`.

## Reaching the UI from another machine

The port binds to `127.0.0.1` by default. To expose it on the network, set in
`.env`:

```dotenv
COMFY_HOST_BIND=0.0.0.0
```

ComfyUI has no authentication and its API can read and write files on the host
volumes. Only do this on a network you trust.

## Running a workflow

If you set `COMFY_ASSET_PROFILES=z-image-turbo-core` above, open the
`image_z_image_turbo` template from Workflow → Browse Templates and queue it.

[docs/workflows.md](docs/workflows.md) lists every provisioned workflow with its
profile name, disk cost and measured run time. To add one, put its profile in
`.env` and recreate the container:

```bash
docker compose up -d          # recreates, so the new .env is read
docker logs -f comfyui        # the models download before the server starts
```

Profiles are comma-separated and share their common files, so two profiles
usually cost far less than the sum of their sizes.

## Host tuning (most likely optional)

This is from the original repo, however I have not needed it in my setup:

`./spark-helper.sh` turns off swap and enables GPU persistence mode, which some
report is needed to keep long video renders from crashing. Neither setting
survives a reboot, so run it again after one. It requires `sudo` and then runs
a temperature and memory monitor in the foreground until interrupted. Adapted
from
[mmartial/ComfyUI-Nvidia-Docker](https://github.com/mmartial/ComfyUI-Nvidia-Docker/blob/main/extras/dgx_spark-helper.sh).

## Repository layout

| Path | What it is |
| --- | --- |
| `Dockerfile` | CUDA 13.0 image; prefers the committed wheels, builds from source when none fit |
| `docker-compose.yml` | service definition, volumes, GPU reservation, healthcheck |
| `entrypoint.sh` | runs on every start: venv, deps, custom nodes, patches, asset bootstrap |
| `.env.example` | template for `.env` |
| `constraints.txt` | the pinned torch / torchvision / torchaudio versions |
| `asset-profiles.json` | the 53 profiles and the files each one downloads |
| `scripts/bootstrap_comfy_assets.sh` | resolves and downloads those files |
| `scripts/smoke/` | offline manifest validation and in-container smoke lanes |
| `patches/` | startup patches applied to ComfyUI and to cloned node packs |
| `custom_nodes/custom_nodes.txt` | the list of node repositories to clone |
| `custom_nodes/ComfyUI-DGX-Spark-Templates/` | the 20 templates this repo bundles |
| `DGX-Spark-WheelsBuilder/` | builds and exports the aarch64 wheels the image consumes |
| `models-cutter/` | splits a monolithic `.safetensors` into ComfyUI's expected files |
| `ComfyUI/` | submodule, `jdaln/ComfyUI` branch `dgx-state` |

## Documentation

| Page | Answers |
| --- | --- |
| [workflows.md](docs/workflows.md) | What can I run, what does it cost, how long does it take |
| [configuration.md](docs/configuration.md) | What goes in `.env`, what is mounted where |
| [models.md](docs/models.md) | How models get downloaded, which ones need a Hugging Face token |
| [troubleshooting.md](docs/troubleshooting.md) | It failed, now what |
| [verifying.md](docs/verifying.md) | Checking that a profile and its workflow actually work |
| [maintenance.md](docs/maintenance.md) | Rebuilding wheels, bumping PyTorch and ComfyUI |

## Credits

Original project by Viktor Grigorev
([dr-vij](https://github.com/dr-vij/ComfyUI-DGX-Spark-Docker-opinionated)),
including the CUDA 13.0 image, the wheel builder and the custom-node control
variables.

## License

This repository provides configuration and Docker wiring only. ComfyUI has its
own [license](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE). Every
model, wheel and third-party library carries its own terms; check each upstream
before using it.
