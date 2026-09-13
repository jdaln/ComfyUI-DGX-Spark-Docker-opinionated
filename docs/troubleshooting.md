# Troubleshooting

## Start here

| What you see | Likely cause | Go to |
| --- | --- | --- |
| Port 8188 refuses connections after `docker compose up` | Asset bootstrap is still running; the server starts after it | [Nothing is listening](#nothing-is-listening-yet) |
| `model: 'X' not in ['No models found']` | The model directory does not exist yet | [Models never downloaded](#models-never-downloaded) |
| A model family is empty after a full start | Provisioning never ran for it | [Models never downloaded](#models-never-downloaded) |
| `403` or an auth warning in the download log | Gated repository, token missing or not approved | [Gated download refused](#gated-download-refused) |
| `weight_dtype: 'bf16' not in [...]` | Template predates a node schema change | [Widget value not in list](#widget-value-not-in-list) |
| `FileNotFoundError` on a path that looks almost right | Two layers appended the same path segment | [Path segment joined twice](#path-segment-joined-twice) |
| Tensor size mismatch inside an `...Inplace` node | Two latents being merged have different lengths | [Latent length mismatch](#latent-length-mismatch) |
| `[Errno 21] Is a directory: '.../input'` | The workflow needs a file you have not supplied | [Workflow needs your own input](#workflow-needs-your-own-input) |
| Black images, or a hang at `Requested to load WanVAE` | The SAM3 pack | [Black output or a VAE hang](#black-output-or-a-vae-hang) |
| Free memory never returns after a render | ComfyUI still holds the model | [Memory stays used after a run](#memory-stays-used-after-a-run) |
| The whole machine locks up or reboots during a big run | Two large models resident at once | [Memory stays used after a run](#memory-stays-used-after-a-run) |
| A model that exists nowhere | The weights may not be published | [Weights not published yet](#weights-not-published-yet) |

## Nothing is listening yet

ComfyUI starts after dependency installation and asset bootstrap finish. On a
first run with a video profile selected that is tens of minutes. The compose
healthcheck allows 30 minutes before reporting unhealthy.

```bash
docker logs -f comfyui
docker exec comfyui bash -lc "curl -sf http://127.0.0.1:8188/system_stats -m 5 >/dev/null && echo UP || echo DOWN"
```

If the UI is unreachable from another machine but `UP` inside the container,
`COMFY_HOST_BIND` is still `127.0.0.1`. See [configuration.md](configuration.md).

## Models never downloaded

Check whether the directory exists at all before assuming the workflow is
broken:

```bash
docker exec comfyui bash -lc "ls /workspace/ComfyUI/models/<expected family>"
```

Empty or missing means provisioning never ran for that family. Check `.env`:

- `COMFY_ASSET_PROFILES` must name the profile that provisions it. See
  [workflows.md](workflows.md).
- `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` must contain only custom node
  **directory** names. Profile names there download nothing. Startup warns about
  entries matching no directory, so grep the log for `WARNING:` first.

Then recreate the container. A restart keeps the old environment:

```bash
docker compose up -d
```

This failure looks exactly like a broken workflow. It has produced
`FileNotFoundError` and `model not in list` across fourteen workflows at once
while every graph and node type was fine.

## Gated download refused

The log warns that Hugging Face access may be gated. Set `HF_TOKEN` in `.env`
for an account that has accepted that specific repository's licence. Approval is
per repository. The current list is in [models.md](models.md).

## Widget value not in list

```
weight_dtype: 'bf16' not in ['default', 'fp8_e4m3fn', 'fp8_e4m3fn_fast', 'fp8_e5m2']
```

The template was authored against an older node schema. Core's `UNETLoader` in
`ComfyUI/nodes.py` used to accept `bf16` and no longer does; `default` loads the
file's own dtype, and bf16 safetensors load as bf16 either way.

Fix: open the failing node's `INPUT_TYPES` or `define_schema` in its source
file, compare the allowed values against the workflow's `widgets_values`, and
correct the value in the JSON. This is a data fix, not a code change.

## Path segment joined twice

```
Failed to load HeartMuLa Transcriptor: Expected to find checkpoint for
HeartTranscriptor at .../HeartMuLa/HeartTranscriptor-oss/HeartTranscriptor-oss
but not found.
```

Two layers each appended the same segment. `resolve_model_path()` in
`ComfyUI-HeartMuLa/nodes.py` resolves the widget value to the real model folder,
then `HeartTranscriptorPipeline.from_pretrained()` joins `HeartTranscriptor-oss`
onto whatever it is given.

Fix: trace every function the widget value passes through between the widget and
the final `os.path.exists` or `from_pretrained` call, not only the first, and
compute the resulting path by hand at each step. Compare against the real layout
with `find /workspace/ComfyUI/models/<family> -maxdepth 3`. In this case the
widget had to name the parent folder so the pipeline's own join landed correctly.

## Latent length mismatch

```
The expanded size of the tensor (10) must match the existing size (25) at
non-singleton dimension 2. ... LTXVImgToVideoInplace
```

`LTXVImgToVideoInplace.execute()` in `ComfyUI/comfy_extras/nodes_lt.py` does
`samples[:, :, :t.shape[2]] = t`. It only works when the source-encoded latent
has fewer or equal temporal frames than the target latent.

Four BFS LTX-2.3 templates computed their target length from fixed `Duration` x
`Frame Rate` primitives, decoupled from the clip actually loaded. A 3 s x 25 fps
target is 10 latent frames; the default `bedroom.mp4` at 200 frames and 30 fps
encodes to 25.

Fix: find the two tensors being merged, trace each back through the graph's
`links` array to whatever sets its length, and make target >= source. Prefer
raising a length-controlling default over rewiring the graph, unless the
mismatch must also hold for arbitrary user clips.

## Workflow needs your own input

```
[Errno 21] Is a directory: '/workspace/ComfyUI/input'
```

The `LoadAudio` or `LoadImage` widget default is empty by design, for templates
the repo ships no sample for. Supply a file and patch a scratch copy of the
workflow, never the checked-in default:

```bash
docker cp your-file.mp3 comfyui:/workspace/ComfyUI/input/your-file.mp3
python3 -c "
import json
wf = json.load(open('custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<name>.json'))
for n in wf['nodes']:
    if n['type'] in ('LoadAudio', 'LoadImage'):
        n['widgets_values'] = ['your-file.mp3']
json.dump(wf, open('/tmp/scratch.json', 'w'))"
docker cp /tmp/scratch.json comfyui:/tmp/scratch.json
docker exec comfyui python3 -u /tmp/wf_smoke.py /tmp/scratch.json 1800
```

## Black output or a VAE hang

On recent ComfyUI builds the SAM3 pack causes black images and hangs around
`Requested to load WanVAE`. Disable that pack and keep the rest:

```dotenv
DISABLE_ALL_CUSTOM_NODES=false
COMFY_NODE_BLACKLIST=ComfyUI-SAM3-DGX-Spark
```

```bash
docker compose down && docker compose up -d
```

The name must match the directory under `custom_nodes/`, not the pack's
project name. This one clones from `dr-vij/ComfyUI-SAM3-DGX-Spark`, so the
directory is `ComfyUI-SAM3-DGX-Spark`. A name that matches no directory
disables nothing; startup warns when that happens, and `ls custom_nodes/` is the
authoritative list.

## Memory stays used after a run

ComfyUI keeps the last model resident so a rerun skips the load. On a discrete
GPU that is free; on a Spark the GPU shares host memory, so a finished 40 GB
video render keeps 40 GB away from everything else until something unloads it.
It does not time out on its own.

Check what is actually held, and reclaim it:

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

docker exec -i comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/free',
      data=json.dumps({'unload_models': True, 'free_memory': True}).encode(),
      headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=60).read()"
```

Two things do this automatically:

- `COMFY_IDLE_UNLOAD_MINUTES` (default 60) unloads after an idle spell. See
  [configuration.md](configuration.md).
- `run_lanes.py` frees before every lane and once at the end.

The failure this prevents is worse than wasted memory. Running two large
profiles back to back without unloading asks for both models at once, and a
Spark answers that by locking up rather than raising an OOM. A three-lane
MiniMax batch took this machine down that way; the driver logged
`NVRM ... NV_ERR_NO_MEMORY` and the box hard-rebooted. Individually each lane
peaks near 40 GB and finishes with 20 GB to spare.

## Weights not published yet

Before accepting that a model is unavailable, search Hugging Face directly and
check the author's profile for the exact repository the template expects.
Availability changes. If nothing turns up, record it in
`scripts/smoke/pending_models.json` with a link to watch, and leave the loader
alone. Do not substitute a different file.

## Reading a failure you do not recognise

The traceback names a `node_id` and a `class_type`. Grep that class in
`custom_nodes/<pack>/**/*.py` or `ComfyUI/comfy_extras/*.py`, read what it does
with its actual inputs, and compute the real values by hand: frame counts, path
joins, dtype lists. A similar-looking template that solves a different
sub-problem is not evidence that the same fix applies.

## Command reference

```bash
# Is the server accepting requests, or still provisioning?
docker exec comfyui bash -lc "curl -sf http://127.0.0.1:8188/system_stats -m 5 >/dev/null && echo UP || echo DOWN"

# What actually downloaded
docker exec comfyui bash -lc "du -sh /workspace/ComfyUI/models/<family>"

# Clear stale queued prompts before a rerun
docker exec comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/queue', data=json.dumps({'clear': True}).encode(), headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=30).read()"

# Most recent outputs
docker exec comfyui bash -lc "ls -lt /workspace/ComfyUI/output | head"
```
