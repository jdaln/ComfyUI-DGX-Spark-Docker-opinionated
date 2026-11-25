# Models and provisioning

Models are not in the image and not in git. The container downloads them at
startup, into `../ComfyData/models/` on the host. ComfyUI's HTTP server does not
start until that finishes.

## The two download paths

Two mechanisms put files under `ComfyUI/models/`. Both can run in the same
start, and neither knows about the other.

| | Asset profiles | Example-workflow scan |
| --- | --- | --- |
| Driven by | `COMFY_ASSET_PROFILES` | `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` |
| Reads | `asset-profiles.json` | `custom_nodes/<pack>/example_workflows/*.json` |
| Picks files from | an explicit list per profile | `properties.models` metadata, or any Hugging Face repo link in the file |
| Predictable | yes | no |

Both are implemented in
[`scripts/bootstrap_comfy_assets.sh`](../scripts/bootstrap_comfy_assets.sh).

Prefer profiles. Use them for anything you want to be able to reproduce or test.

## Asset profiles

A profile is a named set of file groups in `asset-profiles.json`. There are 105.
Each entry is a direct download, a symlink to a file another group provides, or
a whole Hugging Face repo snapshot. Groups are shared, so two profiles that use
the same text encoder download it once.

```dotenv
COMFY_ASSET_PROFILES=krea-2-turbo,lucida-background-removal
```

[workflows.md](workflows.md) maps every profile to the workflow it serves, its
disk cost and its measured run time. Selecting a profile can also make a
matching custom node's example workflows visible, where the manifest's
`selection_metadata` declares that link.

Downloads resume. Partial files are kept as `.part` and progress is printed to
the container log, so `docker logs -f comfyui` shows movement during a
multi-gigabyte pull.

## The allowlist takes directory names

`COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` takes **custom node directory
names** such as `ComfyUI-DGX-Spark-Templates` or `VibeVoice-ComfyUI`. It never
takes profile names.

A profile name in that variable matches no directory and downloads nothing. Put
your profile list in the wrong variable and every workflow that depends on it
fails with what look like workflow bugs. Startup warns about entries that match
no directory, naming the ones that are asset profiles, so read the log:

```
WARNING: COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST lists 'heartmula-oss-3b',
which is an asset profile, not a custom node directory.
```

## Templates that download without a profile

`ComfyUI-DGX-Spark-Templates` is in the default allowlist, so this repo's 78
bundled templates provision themselves even with `COMFY_ASSET_PROFILES` empty:
every one declares its models in `properties.models`. On a fresh machine that is
about 685 GB before the server starts, plus whatever the selected profiles add.
Models from gated repositories download only after their licence is accepted;
each such template says so in its note.
A bare Hugging Face repo link anywhere in a template file is enough for the
resolver to match a loader's filename against that repo.

To see exactly what that pulls before it costs you disk:

```bash
python3 scripts/smoke/validate_manifest.py
```

It prints every file the bundled templates provision with no profile selected,
and warns about any template that can self-provision without declaring it in
`properties.models`. To opt a family out entirely, remove its directory from the
allowlist.

## Gated models

Gated repositories need a Hugging Face account that has accepted the licence
on the repository page, and that account's token in `.env` as `HF_TOKEN`.
`scripts/smoke/plan_downloads.py` reports a repository the token cannot read
as HTTP 401 or 403.

| Repository | Used by | Licence to accept |
| --- | --- | --- |
| [google/gemma-3-12b-it-qat-q4_0-unquantized](https://huggingface.co/google/gemma-3-12b-it-qat-q4_0-unquantized) | every `ltx-2.0-*` profile, via the Gemma 3 text encoder | Gemma, approved by hand |
| [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) | every `ltx-2.5-*` profile, `talking-characters` | LTX-2.x Community License |
| [Lightricks/LTX-2.3-22b-IC-LoRA-HDR](https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-HDR) | `ltx-2.3-iclora-hdr-distilled` | LTX-2 Community License |
| [Lightricks/LTX-2.3-22b-IC-LoRA-DubIt](https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-DubIt) | `ltx-2.3-iclora-lipdub-two-stage-distilled` | LTX-2 Community License |
| [Lightricks/LTX-2.5-22b-IC-LoRA-Ingredients](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Ingredients) | `ltx-2.5-ingredients` | LTX-2.x Community License |
| [Lightricks/LTX-2.5-22b-IC-LoRA-Clean-Plate](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Clean-Plate) | `ltx-2.5-clean-plate` | LTX-2.x Community License |
| [Lightricks/LTX-2.5-22b-IC-LoRA-Alpha-Gen](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Alpha-Gen) | `ltx-2.5-alpha-matte` | LTX-2.x Community License |
| [Lightricks/LTX-2.5-22b-IC-LoRA-Refine-Details](https://huggingface.co/Lightricks/LTX-2.5-22b-IC-LoRA-Refine-Details) | `ltx-2.5-refine-details` (parked, see the plan) | LTX-2.x Community License |
| [Lightricks/LTX-2.3-22b-LoRA-Foley-V2A](https://huggingface.co/Lightricks/LTX-2.3-22b-LoRA-Foley-V2A) | `ltx-2.5-foley` | LTX-2 Community License |
| [black-forest-labs/flux-3-image-klein-comfy](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy) | every `flux-3-klein-*` profile | FLUX Kommunity License |
| [MuScriptor/muscriptor-large](https://huggingface.co/MuScriptor/muscriptor-large) | `muscriptor` | CC BY-NC 4.0 |
| [facebook/sam-3d-body-dinov3](https://huggingface.co/facebook/sam-3d-body-dinov3) | `fab-pose-guide`, for its `model_config.yaml` only | SAM License, approved by hand |

Not gated, but the terms restrict use, several to non-commercial use:

| Repository | Used by | Licence |
| --- | --- | --- |
| [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2) | every `krea-2-*` profile | Krea 2 Community License |
| [Comfy-Org/Ideogram-4](https://huggingface.co/Comfy-Org/Ideogram-4) | `ideogram-4`, `ideogram-4-nvfp4` | Ideogram non-commercial |
| [Comfy-Org/flux2-dev](https://huggingface.co/Comfy-Org/flux2-dev) | `flux2-vae.safetensors` for both Ideogram profiles | FLUX dev non-commercial |
| [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3), [FastVideo/FastH3-Omni-NVFP4-QAD-Comfy](https://huggingface.co/FastVideo/FastH3-Omni-NVFP4-QAD-Comfy) | every `minimax-h3-*` profile, `fasth3-omni` | MiniMax H3 Community License |
| [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | `minimax-h3-image-*` | MiniMax H3 Image Community License |
| [Comfy-Org/sam-3d-body](https://huggingface.co/Comfy-Org/sam-3d-body) | `sam3d-body-*`, `fab-pose-guide` | SAM License |
| [noahcao/FAB](https://huggingface.co/noahcao/FAB) | `fab-pose-guide` | FAB weights licence, non-commercial research |

`fab-pose-guide` also needs SMPL 1.1.0, which no profile can download: register
at [smpl.is.tue.mpg.de](https://smpl.is.tue.mpg.de) and copy the three `.pkl`
files to `models/body_models/smpl/` as `SMPL_NEUTRAL.pkl`, `SMPL_MALE.pkl` and
`SMPL_FEMALE.pkl`.

Approval is per repository. A token accepted for the Gemma repo is still denied
for the Dub-It LoRA until that repo is accepted too.

`ideogram-4-nvfp4` downloads the half-size quants but does not rewire the
blueprint. Switch both model loader selections to the `_nvfp4_mixed` files by
hand after provisioning it.

## Adding models by hand

Drop files into the matching directory under `../ComfyData/models/`. The tree is
ComfyUI's own; these are the ones this setup populates:

```
ComfyData/models/
├── checkpoints/          SD 1.5, SDXL, Flux, single-file checkpoints
├── diffusion_models/     split transformer weights
├── text_encoders/        CLIP, T5, Qwen3-VL, Gemma
├── vae/                  image and video VAEs
├── audio_encoders/       audio VAEs and encoders
├── loras/                LoRAs
├── controlnet/           ControlNet weights
├── clip_vision/          CLIP vision encoders
├── detection/            ONNX pose and object detectors
├── sam2/  sam3/          segmentation checkpoints
├── upscale_models/       upscalers
└── ...
```

## Custom node dependencies are separate

A profile downloads models. It does not install custom nodes. If a workflow
needs a node pack that is not in `custom_nodes/custom_nodes.txt`, add the
repository there yourself.

One known case: the `LTX-2_T2V_Full_wLora` and `LTX-2_I2V_Full_wLora` examples
that ship with `ComfyUI-LTXVideo` need the external `RES4LYF` pack, which no
profile installs. Those two examples are not in the default allowlist, so they
are hidden unless you add `ComfyUI-LTXVideo` to it.
