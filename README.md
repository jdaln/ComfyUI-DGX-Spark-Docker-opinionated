# ComfyDocker

Docker container for running [ComfyUI](https://github.com/Comfy-Org/ComfyUI) with NVIDIA CUDA 13.0 and GPU acceleration.

**Project version: 0.1**

> **Which workflows can I run?** See [WORKFLOWS.md](WORKFLOWS.md) — every provisioned workflow, what it is for, the profile that installs it, and how long it takes.

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

- **flash-attn** — installed from a pre-built wheel inside the image
- **onnxruntime-gpu** — built for CUDA 13.0

If you do not want to rebuild, you can use the ready wheels from `SelfBuiltWheels/` (the container backs up built wheels there).

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
DISABLE_ALL_CUSTOM_NODES=false
COMFY_SHM_SIZE=16g
COMFY_ASSET_PROFILES=wananimate-preprocess
COMFY_NODE_BLACKLIST=ComfyUI-SAM3
```

**Parameters:**
- `UID` — user ID (find with: `id -u`)
- `GID` — group ID (find with: `id -g`)
- `UPDATE_DEPS` — update ComfyUI and custom nodes on each startup (`true`/`false`)
- `COMFY_PORT` — web interface port (default: `8188`)
- `COMFY_SHM_SIZE` — private `/dev/shm` size for the container (default: `16g`)
- `DISABLE_ALL_CUSTOM_NODES` — disable all custom nodes by default (`true`/`false`)
- `COMFY_ASSET_PROFILES` — comma-separated asset profiles to bootstrap inside the container; some profiles also expose the matching custom-node example workflows automatically
- `COMFY_CUSTOM_NODE_MODULES_ALLOWLIST` — expose third-party `custom_nodes/*/example_workflows` templates in the UI (`true`/`false`)
- `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` — comma-separated list of custom node folders whose example workflows remain visible and whose referenced assets should be bootstrapped at container startup
- `HF_TOKEN` — optional Hugging Face token; required for any gated asset profile
- `COMFY_NODE_WHITELIST` — comma-separated list of custom node folders to allow
- `COMFY_NODE_BLACKLIST` — comma-separated list of custom node folders to block

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

### Selective Custom Node Control (env API)

The container supports selective enabling/disabling of custom nodes via `.env`:

- `COMFY_NODE_WHITELIST` — load only listed node folders.
- `COMFY_NODE_BLACKLIST` — load all node folders except listed ones.
- `DISABLE_ALL_CUSTOM_NODES=true` — disable all custom nodes if whitelist/blacklist is not set.

Example blacklist (disable one problematic node, keep the rest):

```dotenv
DISABLE_ALL_CUSTOM_NODES=false
COMFY_NODE_BLACKLIST=ComfyUI-SAM3
```

Priority:
1. `COMFY_NODE_WHITELIST`
2. `COMFY_NODE_BLACKLIST`
3. `DISABLE_ALL_CUSTOM_NODES`

### Custom Node Example Workflows

The default container startup exposes a curated set of third-party example workflow families:

- `ComfyUI-WanVideoWrapper`
- `ComfyUI-KJNodes`
- `ComfyUI-WanAnimatePreprocess`
- `ComfyUI-qwenmultiangle`
- `ComfyUI-DGX-Spark-Templates` — this repo's own bundled workflows

`ComfyUI-DGX-Spark-Templates` is a stub node pack that exists purely to surface workflows this repo provisions but upstream does not ship a template for:

| Template | Why it is here |
| --- | --- |
| Text to Image (Krea 2 Turbo NVFP4) | NVFP4 variant of the bundled turbo template |
| Text to Image (Ideogram v4 NVFP4) | NVFP4 Ideogram 4 with the dual-model guider |
| Text to Image (Krea 2 RAW) | `krea-2-raw` ships a checkpoint no bundled workflow references (52 steps, cfg 1) |
| Text to Image (Krea 2 Turbo Style LoRA) | the nine `krea2_*` style LoRAs are otherwise never exercised |
| Remove Background (Lucida) | the bundled BiRefNet blueprint only offers the base checkpoint |
| Video Edit Anything (LTX-2.3) | the BFS edit LoRAs have no bundled workflow of their own |
| Video Style Swap (LTX-2.3 Anime2Real) | same, for the anime ⇄ live-action pair |
| Video Inpainting (LTX-2.3 Masked) | same, for both masked inpainting LoRAs |
| Video Head Swap (LTX-2.3) | same, for the LTX-2.3 head-swap LoRA |
| Multishot ShotPlan (LTX-2.3) | wires the new BFSNodes multishot builder the way its own docs prescribe |
| Image Edit (Mage-Flow) | core supports Mage-Flow but ships no template for it |
| Image Edit (Mage-Flow Turbo) | same, at the distilled model's 4 steps / cfg 1.0 |
| Text to Music (HeartMuLa 3B) | folds the tag vocabulary from a second HeartMuLa wrapper into the node pack we install |
| Lyrics Transcription (HeartMuLa) | pairs with the above for transcribe → edit → regenerate |
| Text to Speech (Multi-Character Conversation) | VibeVoice ships examples, but not one wired for four cloned voices with pause tags |
| Text to Speech (LTX-2.3 Prompted Voice) | uses LTX-2.3's audio branch as a TTS engine for voices you can only describe |
| Text to Speech (Prompted Voices to Conversation) | joins the two so a described voice becomes a VibeVoice clone source |
| Text to Video (MiniMax H3) | core supports MiniMax H3 as of v0.30.0 but ships no template for it |
| Image to Video (MiniMax H3) | same, driven from a first and optional last frame |
| Reference to Video (MiniMax H3) | same, for the `ref2va` weights and their reference-tag prompting |

**Bundled templates provision themselves, profile or no profile.** Every module in
`COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` is scanned at every startup, and
`ComfyUI-DGX-Spark-Templates` is in the default allowlist, so anything these templates can
resolve gets downloaded even with `COMFY_ASSET_PROFILES` empty. Two mechanisms do it:
embedded `properties.models` metadata, and — less obviously — a bare Hugging Face repo link
anywhere in the file, which is enough for the resolver to match a loader's filename against
that repo's contents.

That is the intended behaviour, but it is easy to acquire by accident, so
`validate_manifest.py` prints the full list of what lands with no profile selected and warns
about any template that can self-provision without declaring it. Read that list before
wondering where the disk went; to opt out of a family entirely, drop the module from the
allowlist.

When a module is present in `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST`, the container now bootstraps its referenced workflow assets during startup using the repo manifest, embedded workflow metadata, and the Hugging Face repo hints shipped with those example JSON files.

Upstream ComfyUI does not read the `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_*` variables yet. Until that feature lands upstream, the entrypoint applies `patches/comfyui/custom-node-example-workflow-gating.patch` to the mounted ComfyUI checkout at startup, together with the other patches in `patches/comfyui/` (each is skipped automatically when its content is already present in the checkout). If a patch does not apply, the container keeps stock ComfyUI behavior for that piece: for this one, every installed custom node's example workflows stay visible, and asset bootstrap still works.

Direct file downloads now leave `.part` files in place for resume and emit periodic size updates to the container log, so `docker logs comfyui` shows startup asset progress instead of looking silent on large pulls.

`COMFY_ASSET_PROFILES` is still the preferred high-level path for repo-owned bundles. Selecting a supported profile also exposes the matching example workflow set automatically.

For example, this profile bootstraps the HunyuanVideo + Leapfusion stack used by the KJNodes example workflow and exposes that example in the template browser:

```dotenv
COMFY_ASSET_PROFILES=leapfusion-hunyuanvideo-i2v
```

To hide all third-party example workflow sets again, set:

```dotenv
COMFY_CUSTOM_NODE_MODULES_ALLOWLIST=false
```

To replace the curated default set with your own selection, set:

```dotenv
COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST=ComfyUI-WanVideoWrapper,ComfyUI-KJNodes
```

To expose every third-party example workflow set regardless of profile selection, set:

```dotenv
COMFY_CUSTOM_NODE_MODULES_ALLOWLIST=true
```

and leave `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` empty.

If you want a different explicit subset instead of the curated default, use:

```dotenv
COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST=ComfyUI-WanVideoWrapper,ComfyUI-KJNodes
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UID` | User ID for running the container | — |
| `GID` | Group ID for running the container | — |
| `UPDATE_DEPS` | Update ComfyUI and custom nodes | `false` |
| `COMFY_PORT` | Web interface port | `8188` |
| `COMFY_SHM_SIZE` | Private shared-memory allocation for the container | `16g` |
| `DISABLE_ALL_CUSTOM_NODES` | Disable all custom nodes (fallback mode) | `true` |
| `COMFY_ASSET_PROFILES` | Comma-separated asset profiles to bootstrap; some profiles also expose the matching custom-node example workflows | — |
| `COMFY_CUSTOM_NODE_MODULES_ALLOWLIST` | Enable the curated third-party example workflow set, or disable all third-party example workflows when set to `false` | `true` |
| `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` | Comma-separated custom node folders whose example workflows stay visible and trigger startup asset bootstrap | `ComfyUI-WanVideoWrapper,ComfyUI-KJNodes,ComfyUI-WanAnimatePreprocess,ComfyUI-qwenmultiangle` |
| `HF_TOKEN` | Hugging Face token used for gated model downloads | — |
| `COMFY_ASSET_MANIFEST_PATH` | Override path to the asset profile manifest inside the container | `/workspace/asset-profiles.json` |
| `WAN_PREPROCESS_VITPOSE_URL` | Override URL for `vitpose-l-wholebody.onnx` | built-in default |
| `WAN_PREPROCESS_YOLO_URL` | Override URL for `yolov10m.onnx` | built-in default |
| `COMFY_NODE_WHITELIST` | Comma-separated custom node folders to allow | — |
| `COMFY_NODE_BLACKLIST` | Comma-separated custom node folders to block | — |

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

The container uses a private `/dev/shm` allocation via `COMFY_SHM_SIZE` instead of `ipc: host`, following the same containment approach used in the main DGX Spark inference stack.

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

### Asset Profiles

Startup can now resolve repo-managed asset profiles from `asset-profiles.json` inside the container. Profiles remain the main opt-in surface for both asset bootstrap and any matching custom-node example workflow exposure.

Current bundled profiles:

- `wananimate-preprocess` — downloads the helper detection assets used by the bundled WanAnimate preprocessing templates
- `ltx-2.0-*` and `ltx-2.3-*` — opt-in LTX workflow asset profiles for the explicitly bundled LTX templates
- `leapfusion-hunyuanvideo-i2v` — downloads the HunyuanVideo + Leapfusion assets and sample input expected by the KJNodes Leapfusion image-to-video example, then exposes that example workflow in the template browser
- `krea-2-turbo`, `krea-2-turbo-styleloras`, `krea-2-turbo-nvfp4`, `krea-2-raw` — opt-in Krea 2 text-to-image profiles (Turbo fp8 is the standard path; `-styleloras` adds the nine official style LoRAs, `-nvfp4` is the half-size Blackwell-optimized quant, `-raw` is the 52-step undistilled base). Krea 2 requires a ComfyUI checkout from 2026-06-22 or newer — newer than the currently pinned submodule commit — and all Krea 2 downloads are gated (see below)
- `vibevoice-1.5b`, `vibevoice-large`, `ltx-2.3-tts-prompted-voice`, `tts-prompted-conversation` — multi-character text to speech. VibeVoice (via [Enemyx-net/VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI)) does up to four speakers in one script but needs an audio sample per voice; LTX-2.3 can be told in words what a voice sounds like but renders one line at a time. Both ship, plus a third template that feeds an LTX-2.3-described voice into VibeVoice as the clone source. `ltx-2.3-tts-prompted-voice` reuses the LTX-2.3 base groups and adds no new weights. Tokenizer files come from `Qwen/Qwen2.5-1.5B` as four individual files rather than a snapshot, to avoid 2.9 GB of weights VibeVoice never loads. Nothing gated
- `heartmula-oss-3b`, `heartmula-transcribe` — the HeartMuLa 3B music model (15 GB generator + 6 GB codec) and its sung-lyrics transcriber (2.9 GB), all ungated, downloaded as Hugging Face snapshots into `models/HeartMuLa/`. Uses [BobRandomNumber/ComfyUI-HeartMuLa](https://github.com/BobRandomNumber/ComfyUI-HeartMuLa), whose separate LLM and codec loaders keep the two models from being resident at once
- `mage-flow-edit`, `mage-flow-edit-turbo` — Microsoft Mage-Flow-Edit from the ungated [Comfy-Org/Mage-Flow](https://huggingface.co/Comfy-Org/Mage-Flow) repo. ComfyUI supports Mage-Flow in core (`comfy/ldm/mage_flow`, the `mage` CLIP type, `TextEncodeMageFlowEdit`), so no custom node is installed. Turbo is 4 steps at cfg 1.0, the base model 30 steps at cfg 5.0, both bf16. The official `int8_convrot` quants are deliberately not provisioned: they are half the size and a straight quality loss on a machine with this much memory, and having both builds on disk is how you end up with two copies of Mage-Flow. There is no ComfyUI-loadable NVFP4 build — the NVFP4 releases are standalone Diffusers pipelines with prebuilt CUDA kernels
- `bfs-ltx-2.3-*` — six LTX-2.3 task LoRAs from [ComfyUI-BFSNodes](https://github.com/alisson-anjos/ComfyUI-BFSNodes): `-edit-anything` (instruction-driven clip edits), `-style-swap` (anime ⇄ live action), `-inpaint` and `-masked-ref-inpaint`, `-head-swap`, and `-multishot` (ShotPlan multi-shot planning). All six reuse the same LTX-2.3 base groups as `ltx-2.3-t2v-i2v-two-stage-distilled`, so they cost one 0.3–1.3 GB LoRA each on top of a base you may already have. The `-multishot` LoRA is not published upstream yet — see `scripts/smoke/pending_models.json`
- `lucida-background-removal` — the Lucida BiRefNet-HR fine-tune (0.9 GB, ungated) plus a bundled `Remove Background (Lucida)` template. Aimed at the mattes plain BiRefNet struggles with: semi-transparent objects, camouflage, text and logos with shadows, illustrations and print designs. Adapted from [egeorcun/lucida](https://github.com/egeorcun/lucida); the nodes are ComfyUI core, so nothing extra is installed
- `minimax-h3-t2v`, `minimax-h3-i2v`, `minimax-h3-ref2v` — MiniMax H3 from the ungated [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) repo, the first workflows here that generate audio. H3 is omni-modal: it emits video and native stereo audio — dialogue, effects and score — in a single forward pass, so the sampler produces one packed audio+video latent that `VAEDecode` and `VAEDecodeAudio` each unpack their own half of. Supported in ComfyUI core as of v0.30.0 (`comfy/ldm/minimax`, `comfy_extras/nodes_minimax_h3.py`), so no custom node is installed. Each profile is ~42 GB: a shared 21 GB support set (Qwen3-VL-32B text encoder in NVFP4 AWQ, plus the video and audio VAEs) and one 21 GB diffusion model. `-t2v` and `-i2v` share the `fl2va` weights and so cost nothing extra once either is installed; `-ref2v` pulls the separate `ref2va` weights, putting all three at 63 GB. The diffusion models are the pruned `int8_convrot` builds the upstream templates select — core gained int8 convrot embedding lookup in the same release — and the bf16 builds are deliberately not provisioned at 66 GB each
- `ideogram-4`, `ideogram-4-nvfp4` — opt-in Ideogram 4 text-to-image profiles for the bundled `Text to Image (Ideogram v4)` blueprint. `ideogram-4` downloads exactly the fp8 files the blueprint references (two diffusion models — conditional and unconditional — plus the Qwen3-VL-8B text encoder and Flux 2 VAE, ~30GB); `ideogram-4-nvfp4` fetches the half-size nvfp4 quants instead, which requires switching the two model-loader selections in the blueprint by hand. Like Krea 2, Ideogram 4 needs a ComfyUI checkout newer than the pinned submodule commit, and most downloads are gated (see below)

Note: the LTX profile layer currently bootstraps the model assets referenced by the bundled templates. The two `LTX-2_*_Full_wLora` workflows still also expect the external `RES4LYF` custom node, which is not auto-added by the asset profile system.

The current profile system bootstraps model/helper assets and can expose matching third-party example workflows when the manifest explicitly maps them. If a workflow also depends on an extra custom node repository that is not already in `custom_nodes/custom_nodes.txt`, that node dependency still needs to be added separately.

The LTX 2.0 profiles keep the official upstream Google Gemma repo-snapshot layout expected by the bundled 2.0 example workflows. The LTX 2.3 profiles use the official `Comfy-Org/ltx-2` split text-encoder file and add the local alias expected by the bundled 2.3 example workflows.

The bundled LTX blueprints are aligned at container startup (via `patches/comfyui/ltx-blueprint-profile-alignment.patch`) so `Text to Video (LTX-2.3)` and `Image to Video (LTX-2.3)` default to the same checkpoint and LoRA filenames that the `ltx-2.3-*` asset profiles bootstrap. For direct `/prompt` execution, use the tracked API graph at `ComfyUI/tests/inference/graphs/ltx23_text_to_video_smoke.json` instead of the UI blueprint wrapper; it is the validated low-cost smoke path and expects `example.png` in `ComfyUI/input/`.

Bundled-template coverage is tracked in `ComfyUI/tests/inference/bundled_template_coverage.json` (installed at startup via `patches/comfyui/bundled-template-smoke-harness.patch`). That manifest scans the current `ComfyUI/blueprints` directory at runtime and applies per-template overrides for validated, blocked, smoke-enabled, and manual-prerequisite entries so new bundled templates do not silently fall out of coverage reporting.

List the current bundled-template coverage matrix from inside the container:

```bash
docker exec comfyui python /workspace/ComfyUI/tests/inference/run_bundled_template_smokes.py --list
```

Run every currently enabled container-only smoke path from inside the container:

```bash
docker exec comfyui python /workspace/ComfyUI/tests/inference/run_bundled_template_smokes.py
```

This runner executes only tracked direct `/prompt` graphs. Blueprints that do not yet have a companion API graph remain in the matrix as `todo` or `blocked` until a real smoke path is added.

Some bundled assets are gated on Hugging Face and will not download unless `HF_TOKEN` is set for an account that has accepted access to the corresponding repos. Public assets continue to bootstrap without a token.

Currently gated bundled assets:

- `google/gemma-3-12b-it-qat-q4_0-unquantized` — full snapshot used by `ltx-gemma3-snapshot-text-encoder`, which is required by `ltx-2.0-t2v-distilled`, `ltx-2.0-i2v-distilled`, `ltx-2.0-t2v-full`, `ltx-2.0-i2v-full`, `ltx-2.0-v2v-detailer`, `ltx-2.0-iclora-all-distilled`, and `ltx-2.0-iclora-all-distilled-ref0.5`
- `Lightricks/LTX-2.3-22b-IC-LoRA-HDR` — `ltx-2.3-22b-ic-lora-hdr-0.9.safetensors`, required by `ltx-2.3-iclora-hdr-distilled`
- `Lightricks/LTX-2.3-22b-IC-LoRA-LipDub` — `ltx-2.3-22b-ic-lora-lipdub-0.9.safetensors`, required by `ltx-2.3-iclora-lipdub-two-stage-distilled`
- `Comfy-Org/Krea-2` — every Krea 2 diffusion model, the Qwen3-VL-4B text encoder, and the style LoRAs used by all `krea-2-*` profiles (the Krea 2 Community License must be accepted on Hugging Face; the shared `qwen_image_vae.safetensors` comes from the ungated Qwen-Image repo instead)
- `Comfy-Org/Ideogram-4` — the conditional and unconditional Ideogram 4 diffusion models used by `ideogram-4` and `ideogram-4-nvfp4` (the Ideogram non-commercial model agreement must be accepted on Hugging Face)
- `Comfy-Org/flux2-dev` — `flux2-vae.safetensors`, required by both Ideogram 4 profiles (FLUX dev non-commercial license; the Qwen3-VL-8B text encoder comes from the ungated `Comfy-Org/Qwen3-VL` repo)

Hugging Face approval is per repo. A token that works for the official Gemma repo or the HDR LoRA can still be denied for LipDub until that specific repo access has been accepted.

### Automatic WanAnimate Detection Models

When `COMFY_ASSET_PROFILES=wananimate-preprocess`, startup will create the detection-model directories and download the default example-workflow assets if they are missing:

- `../ComfyData/models/detection/vitpose-l-wholebody.onnx`
- `../ComfyData/models/detection/onnx/yolov10m.onnx`
- `../ComfyData/models/sam2/sam2.1_hiera_base_plus.safetensors`

This keeps the bundled WanAnimate example workflows closer to turnkey on a fresh setup.

Unset `COMFY_ASSET_PROFILES` if you prefer to manage these model files manually.

### Verifying Profiles (smoke lanes)

Every asset profile has a smoke lane that loads a real bundled workflow, queues it and waits for the output — so "the models downloaded" is checked as "the workflow actually runs", not just "the files exist". The harness lives in `scripts/smoke/`:

| File | Purpose |
| --- | --- |
| `wf_smoke.py` | Converts a UI-format workflow (bundled template, blueprint or custom-node example) into an API prompt, expanding subgraphs, then queues it and waits |
| `run_lanes.py` | Runs lanes sequentially and writes an incremental report |
| `lanes.json` | Profile → workflow mapping; an optional third element is a per-lane model substitution map |
| `audit_refs.py` | Checks that every model a lane's workflow loads actually resolves on disk |
| `build_matrix.py` | Cross-checks profile files against the model references in every workflow |
| `validate_manifest.py` | Offline checks on the manifest, the bundled templates and the lanes — no container, no downloads |

Start with the offline check. It runs from the checkout in under a second and catches
the mistakes that would otherwise only surface after a multi-GiB download: a profile
naming a group that does not exist, a symlink pointing at a file nothing downloads, a
template using a node from a pack `custom_nodes.txt` never installs, a broken link in a
workflow graph, or a lane whose workflow loads a model its profile does not provision.

```bash
python3 scripts/smoke/validate_manifest.py
```

It needs nothing but the checkout and the `ComfyUI` submodule, so it also runs in CI on
every push and pull request (`.github/workflows/validate-manifest.yml`).

It also prints every file the bundled templates provision on their own with no profile
selected — the answer to "why is it downloading that?" — and flags any template that can
self-provision without declaring it in `properties.models`.

Node types that come from a custom node rather than ComfyUI core are declared in
`scripts/smoke/external_node_types.json`, which maps each type to the pack that provides
it — so a bundled template can never quietly acquire a dependency the container will not
install. Models a template references on purpose that nothing can provision yet — a node
that shipped ahead of its weights — are declared in `scripts/smoke/pending_models.json`
and reported as warnings rather than failures.

Then run the real thing against the running container:

```bash
docker cp scripts/smoke/wf_smoke.py  comfyui:/tmp/wf_smoke.py
docker cp scripts/smoke/run_lanes.py comfyui:/tmp/run_lanes.py
docker cp scripts/smoke/lanes.json   comfyui:/tmp/lanes.json

# all lanes, or pass profile names to run a subset
docker exec comfyui python3 -u /tmp/run_lanes.py
docker exec comfyui python3 -u /tmp/run_lanes.py krea-2-turbo ideogram-4
```

Results land in `/tmp/lane_report.json` inside the container. Note that a lane passing only proves the graph executed — when a template is new or its sampler settings changed, look at the output image/video too.

A passing lane is not the same guarantee as a working template: the harness can satisfy a missing model from an inner-node default or a stub input, so run the audit as well. It checks that a user who provisions a profile and opens its workflow sees no missing models:

```bash
docker cp scripts/smoke/audit_refs.py comfyui:/tmp/audit_refs.py
docker exec comfyui python3 /tmp/audit_refs.py
```

All 35 profiles listed in the WORKFLOWS.md category tables currently pass both checks.
Profiles under WORKFLOWS.md's *Provisioned — not yet hardware-verified* heading have
passed `validate_manifest.py` but have not had their lane or audit run on a Spark yet;
move a row up once both pass and the output looks right.

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

In `entrypoint.sh`, PyTorch is always installed from the CUDA 13.0 index:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
```

This is an intentional pin for DGX Spark; change the line in `entrypoint.sh` if you want a different version flow.

## Changelog

### 2026-03-07 — v0.1

- Added project versioning (`0.1`)
- Identified and documented regression source on latest ComfyUI stack: `ComfyUI-SAM3`
- Added env-based custom node control API:
  - `COMFY_NODE_WHITELIST`
  - `COMFY_NODE_BLACKLIST`
  - `DISABLE_ALL_CUSTOM_NODES`
- Finalized practical workaround: disable only `ComfyUI-SAM3` via blacklist

If you are on a recent ComfyUI build and get black images / hangs around `Requested to load WanVAE`:

1. Set in `.env`:
   - `DISABLE_ALL_CUSTOM_NODES=false`
   - `COMFY_NODE_BLACKLIST=ComfyUI-SAM3`
2. Restart container: `docker compose down && docker compose up`
3. Re-test the same workflow.

### 2026-02-23 — decord support for ComfyUI-RMBG / SAM3

- Built **decord** wheel from [source](https://github.com/dr-vij/decord) (forked with FFmpeg 7 fixes) with CUDA/NVDEC enabled — no pre-built wheel exists for Python 3.12
- Added decord wheel to `entrypoint.sh` (backup + install on startup, same as flash-attn and onnxruntime-gpu)
- Enabled `video` driver capability in `docker-compose.yml` so the container gets access to hardware video decoding
- Added `libopengl0` to fix a missing library warning

## License

This repo only provides configuration and Docker wiring (and I do not really care about that). ComfyUI has its own license — see [ComfyUI License](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE). All models have their own licenses. All wheels and third-party libraries have their own licenses as well — please check and comply with each upstream.

## Author

dr-vij
