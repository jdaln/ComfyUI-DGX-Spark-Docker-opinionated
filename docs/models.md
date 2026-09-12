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

A profile is a named set of file groups in `asset-profiles.json`. There are 53.
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

`ComfyUI-DGX-Spark-Templates` is in the default allowlist, so this repo's 20
bundled templates provision themselves even with `COMFY_ASSET_PROFILES` empty.
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

Some downloads require a Hugging Face account that has accepted the model's
licence. Set `HF_TOKEN` in `.env` for that account. Public assets download
without a token.

| Repository | Used by | Licence to accept |
| --- | --- | --- |
| `google/gemma-3-12b-it-qat-q4_0-unquantized` | every `ltx-2.0-*` profile, via the Gemma 3 text encoder | Gemma |
| `Lightricks/LTX-2.3-22b-IC-LoRA-HDR` | `ltx-2.3-iclora-hdr-distilled` | Lightricks |
| `Lightricks/LTX-2.3-22b-IC-LoRA-LipDub` | `ltx-2.3-iclora-lipdub-two-stage-distilled` | Lightricks |
| `Comfy-Org/Krea-2` | every `krea-2-*` profile | Krea 2 Community License |
| `Comfy-Org/Ideogram-4` | `ideogram-4`, `ideogram-4-nvfp4` | Ideogram non-commercial |
| `Comfy-Org/flux2-dev` | `flux2-vae.safetensors` for both Ideogram profiles | FLUX dev non-commercial |

Approval is per repository. A token accepted for the Gemma repo is still denied
for the LipDub LoRA until that repo is accepted too.

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
