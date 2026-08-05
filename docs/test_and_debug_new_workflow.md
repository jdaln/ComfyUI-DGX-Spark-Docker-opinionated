# How to Test and Debug a ComfyUI Workflow

Reference doc for this repo's ComfyUI-on-DGX-Spark container: how provisioning
actually works, how to run the smoke toolchain, and a catalog of real bugs
this stack has produced — with root causes traced to source, not guesses.

Two companion skills turn this into on-demand agent procedures:
- [`.github/skills/comfyui-workflow-debug/SKILL.md`](.github/skills/comfyui-workflow-debug/SKILL.md) — triage a failing workflow
- [`.github/skills/comfyui-workflow-integrate/SKILL.md`](.github/skills/comfyui-workflow-integrate/SKILL.md) — onboard a new workflow

---

## 1. The auto-provisioning model

Two independent paths put files under `ComfyUI/models/`. Both can run at once;
neither knows about the other's failures.

**A — Profile-driven** (deterministic, prefer this for testing)
- `.env`: `COMFY_ASSET_PROFILES=profile-a,profile-b`
- Startup (`scripts/bootstrap_comfy_assets.sh`) reads `asset-profiles.json`:
  `profiles[name]` → list of `groups[group]` → list of file/symlink/hf_snapshot
  entries, each downloaded to its `dest`.

**B — Example-workflow module scan** (implicit, easy to misuse)
- `.env`: `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST=ModuleFolderName,...`
- Every module in the allowlist gets its `example_workflows/*.json` scanned.
  A bare Hugging Face repo link anywhere in the file, or a node's embedded
  `properties.models`, is enough to make a template self-provisioning even
  with no profile selected.
- `asset-profiles.json.custom_node_example_workflow_profiles[module]` also
  pulls in whole profiles for that module.

**The allowlist takes custom-node *module folder names*
(`ComfyUI-DGX-Spark-Templates`, `VibeVoice-ComfyUI`) — never profile names.**

### Real incident: allowlist populated with profile names instead of module names

`.env` had `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` filled with a mix of
real module names and profile names (`heartmula-oss-3b`, `vibevoice-large`,
`bfs-ltx-2.3-edit-anything`, ...), and `COMFY_ASSET_PROFILES` was never set at
all. Profile names in the allowlist never match a `custom_nodes/` folder, so
they are silently ignored — no error, no warning, just models that never
download. Every symptom looked like a broken workflow (`FileNotFoundError`,
`model not in list`) but every graph and node type was fine; only the asset
step never ran, for 14 workflows at once.

**Fix:** set `COMFY_ASSET_PROFILES` explicitly to the full profile list you
need, and keep `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` restricted to
actual module folder names. Then **recreate** the container:

```bash
docker compose up -d   # recreates with the new .env; a plain restart keeps the old baked-in env
```

**Diagnostic tell:** if the expected model family is empty after a full
startup cycle, provisioning never ran for it — check `.env` before assuming
the template is broken:

```bash
docker exec comfyui bash -lc "ls /workspace/ComfyUI/models/<expected family>"
```

---

## 2. The smoke-testing toolchain

| Script | Runs where | Needs models on disk | Catches |
| --- | --- | --- | --- |
| `scripts/smoke/validate_manifest.py` | host, offline | no | broken manifest structure, dangling symlink targets, node types no installed pack provides, lane → profile mismatches, undeclared self-provisioning |
| `scripts/smoke/wf_smoke.py <workflow> <timeout> [subs]` | in container | yes | converts a UI-format workflow to an API prompt (expanding subgraphs), queues it, waits for completion — the only script that actually executes a graph |
| `scripts/smoke/run_lanes.py [profiles...]` | in container | yes | runs every (or selected) `lanes.json` entry through `wf_smoke.py`, writes `/tmp/lane_report.json` |
| `scripts/smoke/audit_refs.py [profiles...]` | in container | yes | for each lane, checks every model the workflow *references* actually resolves on disk — catches a lane that only "passed" because the harness stubbed or substituted a missing model |
| `scripts/smoke/build_matrix.py` | host + container | yes | cross-checks profile file sets against workflow model references, for spotting orphaned files or unlisted requirements |

**Passing `run_lanes.py` is not proof of a working template.** The harness can
satisfy a missing model with an inner-node default, a stub `LoadImage`, or a
lane-declared substitution (`lanes.json`'s optional third element). Always
also run `audit_refs.py`, and look at the actual output at least once.

---

## 3. Full verification procedure

```bash
# 0. Offline, no container needed, run this first — it's free
python3 scripts/smoke/validate_manifest.py

# 1. Bring the stack up (recreate if .env changed)
docker compose up -d
docker compose ps
docker logs -f comfyui        # watch provisioning; ComfyUI's HTTP server does
                               # not start until asset bootstrap finishes

# 2. Stage the harness (re-run after every container recreate — /tmp is wiped)
docker cp scripts/smoke/wf_smoke.py   comfyui:/tmp/wf_smoke.py
docker cp scripts/smoke/run_lanes.py  comfyui:/tmp/run_lanes.py
docker cp scripts/smoke/lanes.json    comfyui:/tmp/lanes.json
docker cp scripts/smoke/audit_refs.py comfyui:/tmp/audit_refs.py

# 3. Confirm the server is actually up before testing
docker exec comfyui bash -lc "curl -sf http://127.0.0.1:8188/system_stats -m 5 >/dev/null && echo UP || echo DOWN"

# 4. Clear any stale queued prompts from a previous run
docker exec comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/queue', data=json.dumps({'clear': True}).encode(), headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=30).read()"

# 5. Run the lanes you touched (omit args to run everything)
docker exec comfyui python3 -u /tmp/run_lanes.py profile-a profile-b
docker exec comfyui cat /tmp/lane_report.json

# 6. Audit model resolution for the same profiles
docker exec comfyui python3 /tmp/audit_refs.py profile-a profile-b

# 7. Workflows with no lane (new, pending, or input-dependent): run directly
docker cp "custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<name>.json" comfyui:/tmp/
docker exec comfyui python3 -u /tmp/wf_smoke.py "/tmp/<name>.json" 1800

# 8. Look at the actual output — a pass only proves the graph executed
docker exec comfyui bash -lc "ls -lt /workspace/ComfyUI/output | head"
```

For input-dependent workflows (audio/image the repo doesn't ship a sample
for), copy a real file into `ComfyUI/input/` first, then patch that node's
`widgets_values` in a scratch copy of the workflow JSON before running step 7
— see [the SKILL for the exact pattern](.github/skills/comfyui-workflow-debug/SKILL.md#input-dependent-workflows).

---

## 4. Debugging playbook — real incidents, root cause, fix

Each entry below was found by **reading the failing node's source**, not by
guessing. That is the one technique that generalizes: `class_type` in the
traceback → grep for that class in `custom_nodes/<pack>/` or
`ComfyUI/comfy_extras/` → read what it actually does with its inputs.

### A. Silent non-provisioning (allowlist vs. profile names)
See [§1](#1-the-auto-provisioning-model) above. Symptom is indistinguishable
from a broken template until you check whether the model directory exists at
all.

### B. Validation error: value not in list
```
weight_dtype: 'bf16' not in ['default', 'fp8_e4m3fn', 'fp8_e4m3fn_fast', 'fp8_e5m2']
```
**Cause:** the template was authored against an older node schema. Core's
`UNETLoader` (`ComfyUI/nodes.py`) used to accept `bf16`; current core does not
— `default` loads the file's own dtype (bf16 safetensors load as bf16 either
way).
**Real incident:** both Mage-Flow templates shipped with `weight_dtype:
"bf16"`. Fixed by changing the widget value to `"default"` in both JSON
files — a one-line data fix, not a code change.
**General fix:** open the failing node's `INPUT_TYPES`/schema in its source
file, compare the allowed list against the workflow's `widgets_values`, and
correct the value in the JSON.

### C. Model not in list / No models found
```
model: 'VibeVoice-Large' not in ['No models found']
```
**Cause:** the node's dropdown is built from whatever folders currently exist
under its models root — the folder is simply not there yet.
**Fix:** confirm the profile that provisions this family is in
`COMFY_ASSET_PROFILES` and finished downloading (`docker logs comfyui`), then
re-run. Don't touch the workflow JSON for this one.

### D. Runtime FileNotFoundError — path construction bug in the loader
```
Failed to load HeartMuLa Transcriptor: Expected to find checkpoint for
HeartTranscriptor at .../HeartMuLa/HeartTranscriptor-oss/HeartTranscriptor-oss
but not found.
```
**Cause:** two layers each appended the same path segment. `resolve_model_path()`
in `ComfyUI-HeartMuLa/nodes.py` tries `models_dir/HeartMuLa/{base_path}` first;
`HeartTranscriptorPipeline.from_pretrained()` in
`heartlib/pipelines/lyrics_transcription.py` then joins `"HeartTranscriptor-oss"`
onto *whatever it's given*. The template's `HeartMuLaTranscriptionLoader.base_path`
widget was `"HeartTranscriptor-oss"`, which `resolve_model_path` already
resolves to the real model folder — then the pipeline appended the same
segment again, producing a path nested one level too deep that could never
exist.
**Fix found by:** reading `resolve_model_path` and `from_pretrained` in
sequence, computing by hand what path each one produces for a given input,
and comparing against `find /workspace/ComfyUI/models/HeartMuLa -maxdepth 3`
on the actual downloaded layout. Corrected the widget to `"HeartMuLa"` (the
parent folder) so the pipeline's own join lands on the right path.
**General fix:** when a loader node's error names a path that looks
"almost right", suspect a double-join — trace every function the value passes
through between the widget and the final `os.path.exists`/`from_pretrained`
call, not just the first one.

### E. Runtime tensor shape mismatch in an in-place video node
```
The expanded size of the tensor (10) must match the existing size (25) at
non-singleton dimension 2. ... LTXVImgToVideoInplace
```
**Cause:** `LTXVImgToVideoInplace.execute()` (`ComfyUI/comfy_extras/nodes_lt.py`)
does `samples[:, :, :t.shape[2]] = t` — it only works when the source-encoded
latent (`t`, from the loaded video/image) has **fewer or equal** temporal
frames than the target `EmptyLTXVLatentVideo` latent.
**Real incident:** all four BFS LTX-2.3 video templates (Edit Anything, Style
Swap, Inpaint, Head Swap) compute the target length from fixed `Duration` ×
`Frame Rate` primitives (`3s × 25fps + 1 = 76` pixel frames → 10 latent
frames via LTX's `floor((N-1)/8)+1`), completely decoupled from the loaded
`LoadVideo` clip. The bundled default `bedroom.mp4` (200 frames / 30 fps)
encodes to 25 latent frames by the same formula — the target was too short
and the in-place copy failed on the very first sampling stage.
**Fix found by:** reading `LTXVImgToVideoInplace.execute`, tracing both
latents feeding it back through the graph's `links` array to their sources
(a `ComfyMathExpression` node and a `PrimitiveInt`), then computing both
frame counts by hand. No existing verified LTX-2.3 template in this repo does
a full source-video re-encode + inplace overwrite to copy a pattern from — the
verified ones only condition on a fixed-length single/first-last-frame image
— so the fix had to come from the arithmetic, not an analogy. Raised the
`Duration` primitive default from `3` to `8` (target → 26 ≥ 25 latent frames)
in all four templates: a minimal widget-value change, not a graph rewire.
**General fix:** for any `...Inplace`/latent-merge node failure, find the two
tensors it's merging, trace each to its length source, and make sure
target ≥ source. Prefer bumping a length-controlling default over rewiring
the graph unless the mismatch must also hold for arbitrary user clips.

### F. Input-dependent workflow fails at LoadAudio/LoadImage
```
[Errno 21] Is a directory: '/workspace/ComfyUI/input'
```
**Cause:** the workflow's `LoadAudio`/`LoadImage` widget default is empty —
by design, for templates the repo doesn't ship a sample for (e.g. lyrics
transcription needs a real song).
**Fix:**
```bash
docker cp your-file.mp3 comfyui:/workspace/ComfyUI/input/your-file.mp3
python3 -c "
import json
wf = json.load(open('path/to/template.json'))
for n in wf['nodes']:
    if n['type'] == 'LoadAudio':
        n['widgets_values'] = ['your-file.mp3']
json.dump(wf, open('/tmp/scratch.json', 'w'))"
docker cp /tmp/scratch.json comfyui:/tmp/scratch.json
docker exec comfyui python3 -u /tmp/wf_smoke.py /tmp/scratch.json 1800
```
Don't edit the checked-in template's default for this — leave it empty so
the UI still prompts the user for their own file.

### G. Model genuinely doesn't exist upstream yet
**Real incident:** `bfs-ltx-2.3-multishot` needs
`bfs/multishot_strata_r128_v1.safetensors`. Before accepting "pending" at face
value, verify it: search Hugging Face directly (`huggingface.co/models?search=...`)
and check the author's profile page for the exact repo the template expects.
If nothing turns up, leave it tracked in `scripts/smoke/pending_models.json`
and move on — don't invent a substitute file or silently point the loader at
something else.

---

## 5. Sanity-checking output, not just exit codes

A `COMPLETED` smoke result only proves the graph executed. Before promoting a
workflow to verified:
- Open the produced file (image/video/audio/text) at least once.
- For text/audio pipelines, read or listen to enough of it to confirm it's
  not silence, noise, or a repeated token — this caught nothing here, but
  it's cheap insurance every time a new model is wired in.

---

## 6. Repeatable new-workflow checklist

1. Add/adjust profile groups in `asset-profiles.json`.
2. Add the template JSON under
   `custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/`, with
   `properties.models` on every loader node (name/url/directory).
3. Add a lane in `scripts/smoke/lanes.json`, unless it's genuinely
   input-dependent or blocked upstream (then note it in
   `scripts/smoke/pending_models.json` instead).
4. `python3 scripts/smoke/validate_manifest.py` — fix everything it flags.
5. `run_lanes.py` + `audit_refs.py` for the new profile(s).
6. Manually inspect the output artifact.
7. Promote the row from *Provisioned — not yet hardware-verified* into its
   category table in `WORKFLOWS.md`, filling in the measured Run time.

---

## 7. Command cheatsheet

```bash
# Clear the queue before a rerun
docker exec comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/queue', data=json.dumps({'clear': True}).encode(), headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=30).read()"

# Check what actually downloaded
docker exec comfyui bash -lc "du -sh /workspace/ComfyUI/models/<family> 2>/dev/null"

# Tail the lane report
docker exec comfyui cat /tmp/lane_report.json

# Confirm the server is actually accepting requests (not still provisioning)
docker exec comfyui bash -lc "curl -sf http://127.0.0.1:8188/system_stats -m 5 >/dev/null && echo UP || echo DOWN"
```
