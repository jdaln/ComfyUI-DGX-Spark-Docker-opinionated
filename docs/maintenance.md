# Maintenance

## PyTorch pin

The runtime target is set in two places and both must agree:

| Where | What |
| --- | --- |
| `constraints.txt` | `torch==2.10.0+cu130`, `torchvision==0.25.0+cu130`, `torchaudio==2.10.0+cu130` |
| `entrypoint.sh` | installs from `https://download.pytorch.org/whl/cu130` on every start |

`constraints.txt` is mounted read-only and exported as `PIP_CONSTRAINT`, so it
also holds every custom node's dependency resolution to the same torch build.
Change the version in both files, then rebuild the ABI-sensitive wheels below.

## Wheels

The image needs five wheels that have no upstream build for CUDA 13.0 on Python
3.12 and arm64:

| Wheel | Built where | Installed |
| --- | --- | --- |
| flash-attn, flash-attn 3 | `DGX-Spark-WheelsBuilder`, or from source in `Dockerfile` | every start |
| onnxruntime-gpu | same | after custom node dependencies, so nothing reinstalls the CPU build over it |
| SageAttention | same | only when `COMFY_CMDLINE_EXTRA` contains `--use-sage-attention` |
| decord | built in `Dockerfile` from a fork with FFmpeg 7 fixes | every start |
| comfy-aimdo | committed under `SelfBuiltWheels/` | only when `FORCE_LOCAL_COMFY_AIMDO=true` |

`Dockerfile` copies `DGX-Spark-WheelsBuilder/Wheels/` into the image and prefers
those files. It validates each candidate wheel and falls back to building from
source only when none is usable. A clone without `git-lfs` gets LFS pointers
instead of wheels, which triggers that fallback and adds well over half an hour
to the build.

The container also copies whatever it installed into `SelfBuiltWheels/` on the
host, so you always have the artifacts a working container was built from.

Rebuild after a torch bump or any major dependency change:

```bash
./DGX-Spark-WheelsBuilder/export_wheels.sh   # writes DGX-Spark-WheelsBuilder/Wheels/
docker compose build --no-cache              # also rebuilds decord, which lives in the image
```

`export_wheels.sh` needs Docker `buildx`. Override the image tag with
`IMAGE_TAG=my-tag ./DGX-Spark-WheelsBuilder/export_wheels.sh`. See
[`DGX-Spark-WheelsBuilder/README.md`](../DGX-Spark-WheelsBuilder/README.md).

Commit refreshed wheels through git-lfs; `.gitattributes` already routes
`*.whl` there.

## Startup patches

`entrypoint.sh` applies every patch in `patches/` on each start. A patch whose
content is already present is skipped. A patch that no longer fits logs a
warning and is skipped, costing only its own feature, never startup.

| Patch | Target | What it adds |
| --- | --- | --- |
| `comfyui/custom-node-example-workflow-gating.patch` | `app/custom_node_manager.py` | reads `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST`. Without it every installed pack's examples are visible |
| `comfyui/ltx-blueprint-profile-alignment.patch` | `blueprints/*.json` | points the LTX blueprints at the filenames the `ltx-*` profiles download |
| `comfyui/bundled-template-smoke-harness.patch` | `tests/inference/` | the coverage manifest and API smoke graphs used by [verifying.md](verifying.md) |
| `custom_nodes/wananimate-detection-onnx-extension.patch` | `ComfyUI-WanAnimatePreprocess/nodes.py` | makes the detection ONNX models selectable |
| `custom_nodes/ltxvideo-kornia-pad-fallback.patch` | `ComfyUI-LTXVideo/pyramid_blending.py` | fixes a kornia import that breaks on the pinned version |

Node packs are re-cloned on a fresh machine, so a fix that is not carried as a
patch here is lost. Send the fix upstream as well; delete the patch once it
lands and the pack is pulled.

## ComfyUI submodule

The submodule tracks `jdaln/ComfyUI` branch `dgx-state`, currently at `56f644d`
(v0.30.0, 2026-07-13). It is a fork rather than upstream because the branch
carries changes ahead of a release.

```bash
cd ComfyUI
git fetch origin dgx-state
git checkout dgx-state && git pull
cd ..
git add ComfyUI && git commit -m "core: bump comfyui"
```

After a bump:

1. `python3 scripts/smoke/validate_manifest.py`. It harvests every node id the
   pinned checkout registers, so a node that moved or was renamed shows up here.
2. `docker compose build --no-cache && docker compose up -d`, then read the log
   for patches that no longer apply and for custom nodes that fail to import.
3. Re-run the lanes for the profiles you use. See [verifying.md](verifying.md).

Expect patch warnings when the branch already contains a patch's content. The
patches target the older pin so a fresh clone still gets the behaviour.

## Resetting

Rebuild the Python environment, keeping models:

```bash
docker compose down
rm -rf venv/* pip_cache/*
docker compose up -d
```

Clear the download caches:

```bash
rm -rf hf_cache/* ultralytics_cache/*
```

Delete models, saved workflows, inputs and outputs. This is not recoverable:

```bash
rm -rf ../ComfyData/*
```
