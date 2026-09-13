---
name: comfyui-workflow-debug
description: 'Debug a failing, broken, or missing-model ComfyUI workflow in this DGX Spark ComfyUI Docker repo. Use when: a smoke lane fails, run_lanes.py or audit_refs.py reports a problem, a template errors on load or execution, models silently did not download, provisioning seems stuck, or output from a workflow looks wrong. Covers COMFY_ASSET_PROFILES / COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST provisioning, wf_smoke.py/run_lanes.py/audit_refs.py, and root-cause tracing through node source code.'
---

# Debug a ComfyUI Workflow (this repo)

Deep background, full command reference, and the real-incident catalog live in
[docs/troubleshooting.md](../../docs/troubleshooting.md) and
[docs/verifying.md](../../docs/verifying.md).
This skill is the condensed triage procedure.

## Triage order

**1. Is this actually a provisioning problem, not a workflow bug?**

```bash
docker exec comfyui bash -lc "ls /workspace/ComfyUI/models/<expected family>"
```

Empty or missing → check `.env`:
- `COMFY_ASSET_PROFILES` must list the profile that provisions this family.
- `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` must contain only
  **custom-node module folder names** (e.g. `ComfyUI-DGX-Spark-Templates`),
  never profile names — profile names silently no-op there.
- After editing `.env`, run `docker compose up -d` (recreate, not restart) so
  the new environment is actually read.
- Confirm the server finished booting before testing:
  `docker exec comfyui bash -lc "curl -sf http://127.0.0.1:8188/system_stats -m 5 >/dev/null && echo UP || echo DOWN"`
  — ComfyUI's HTTP server does not start until asset bootstrap finishes, so a
  "connection refused" during a fresh provisioning run is expected, not a bug.

**2. Stage the harness (every fresh container wipes `/tmp`):**

```bash
docker cp scripts/smoke/wf_smoke.py   comfyui:/tmp/wf_smoke.py
docker cp scripts/smoke/run_lanes.py  comfyui:/tmp/run_lanes.py
docker cp scripts/smoke/lanes.json    comfyui:/tmp/lanes.json
docker cp scripts/smoke/audit_refs.py comfyui:/tmp/audit_refs.py
```

**3. Reproduce with the smallest scope:**

```bash
# clear stale queue first
docker exec comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/queue', data=json.dumps({'clear': True}).encode(), headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=30).read()"

docker exec comfyui python3 -u /tmp/run_lanes.py <profile>
docker exec comfyui python3 /tmp/audit_refs.py <profile>
```

For a workflow with no lane, copy it and run `wf_smoke.py` directly:

```bash
docker cp "custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<name>.json" comfyui:/tmp/
docker exec comfyui python3 -u /tmp/wf_smoke.py "/tmp/<name>.json" 1800
```

**4. Classify the error by signature, then trace to source — never guess:**

| Signature | Likely cause | Where to look |
| --- | --- | --- |
| `value not in list` (e.g. `weight_dtype: 'bf16' not in [...]`) | Template widget value predates a node schema change | The node's `INPUT_TYPES`/`define_schema` in its source file — compare allowed values |
| `model: 'X' not in ['No models found']` | Model folder genuinely not on disk yet | Re-check step 1; don't touch the workflow JSON |
| `FileNotFoundError` naming a path that looks *almost* right | A path segment got appended twice by two different layers | Read every function the widget value passes through, in order, and compute the resulting path by hand at each step |
| Tensor/shape mismatch inside an `...Inplace` or latent-merge node | Two latents being merged have incompatible lengths | Trace both latents back through the graph's `links` array to their length sources; target must be ≥ source |
| `Is a directory: '.../input'` from `LoadAudio`/`LoadImage` | Widget default is intentionally empty; workflow needs a real file | Copy a real file into `ComfyUI/input/`, patch a **scratch copy** of the JSON, never the checked-in default |
| Missing model with no working download URL anywhere | Weights may not be published yet | Search Hugging Face directly before accepting "pending"; check `scripts/smoke/pending_models.json` |
| `403` on every file of one repo, token valid elsewhere | That repo's licence is not accepted | `gated: auto` means instant approval, not no approval. HEAD-probe the repo with the token; accept the licence on its HF page |
| `einops` "can't divide axis of length N in chunks of 2" | Source dimensions not divisible by what the patchifier needs | LTX latents are pixels/4 then patched 2x2, so pixel dims must divide by 8, and 32 is the safe snap. Insert a resize rather than changing the source |
| A widget receives its neighbour's value (`invalid literal for int()`, a combo string in a float) | `widgets_values` mapped positionally against a mismatched schema | Compare `len(widgets_values)` to required+optional widget-bearing inputs from `/object_info`. Run the upstream original through `wf_smoke.py` to prove it is the node, not your edit |
| Free memory never returns after a render; box locks up on the next big model | ComfyUI keeps the last model resident | POST `/free`; see the memory section in docs/troubleshooting.md |

**Before blaming the workflow, check memory.** ComfyUI does not release a
model when a prompt finishes. Two large profiles run back to back ask for both
at once, and a Spark answers that by locking up rather than raising an OOM.
`run_lanes.py` now frees between lanes and `COMFY_IDLE_UNLOAD_MINUTES` handles
the GUI case, but a hand-run sequence still needs:

```bash
docker exec -i comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/free',
      data=json.dumps({'unload_models': True, 'free_memory': True}).encode(),
      headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=60).read()"
```

Watch `MemAvailable` during an unfamiliar workflow rather than discovering the
floor by crashing: LTX 2.5 bottoms out near 14 GB of 121 GB, MiniMax H3 near
21 GB.

**The one technique that always works:** the traceback names a `node_id` and
`class_type`. Grep that class name in `custom_nodes/<pack>/**/*.py` or
`ComfyUI/comfy_extras/*.py`, read what it does with its actual inputs, and
compute the real values by hand (frame counts, path joins, dtype lists) rather
than pattern-matching against a similar-looking template. A "same family"
template that solves a *different* sub-problem is not evidence the same fix
applies here — verify structurally before copying a pattern.

**5. Fix at the smallest safe scope.** Prefer a one-value widget/default
correction in the template JSON over rewiring the graph, unless correctness
genuinely requires new node connections. Never use `wf_smoke.py`'s
substitution mechanism to paper over a real missing model or template bug —
that only hides the problem from the next person.

**6. Re-run lane + audit, then look at the actual output file once** (image,
video, audio, or text) before calling it fixed. A `COMPLETED` result only
proves the graph executed.

## Input-dependent workflows

```bash
docker cp your-file.ext comfyui:/workspace/ComfyUI/input/your-file.ext
python3 -c "
import json
wf = json.load(open('custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<name>.json'))
for n in wf['nodes']:
    if n['type'] in ('LoadAudio', 'LoadImage'):
        n['widgets_values'] = ['your-file.ext']
json.dump(wf, open('/tmp/scratch.json', 'w'))"
docker cp /tmp/scratch.json comfyui:/tmp/scratch.json
docker exec comfyui python3 -u /tmp/wf_smoke.py /tmp/scratch.json 1800
```
