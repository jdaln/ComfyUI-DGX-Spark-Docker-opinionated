---
name: comfyui-workflow-integrate
description: 'Integrate or onboard a new workflow (bundled template, blueprint, or custom-node example) into this DGX Spark ComfyUI Docker repo. Use when: adding a new ComfyUI template, wiring a new asset profile, adding smoke-lane coverage for a workflow, or promoting a workflow from "not yet hardware-verified" to verified in docs/workflows.md. Covers asset-profiles.json schema, properties.models embedding, lanes.json, and the validate/run/audit toolchain.'
---

# Integrate a New Workflow (this repo)

Background on the provisioning model and the smoke toolchain lives in
[docs/models.md](../../docs/models.md) and
[docs/verifying.md](../../docs/verifying.md).
This skill is the step-by-step onboarding checklist.

## 0. Check what core already does — before writing anything

Two sessions running, the first draft of a new template duplicated something
ComfyUI core already shipped. Do these three checks first; they take a minute
and can cancel the whole task.

**Does core already ship a template?**

```bash
docker exec -i comfyui python3 -c "
import json, glob
p = glob.glob('/workspace/venv/lib/python3*/site-packages/comfyui_workflow_templates_json/templates/index.json')[0]
idx = json.load(open(p))
for c in idx:
    for t in c.get('templates', []):
        if 'YOUR_MODEL' in t['name'].lower(): print(t['name'], '->', t.get('title'))"
ls ComfyUI/blueprints | grep -i YOUR_MODEL
```

If it does, provision for *that* and reference it by bare name in a lane. Only
bundle your own when core has nothing, or when you need a variant core does not
cover (an NVFP4 build, a different checkpoint). Check what core's template
already contains too: LTX 2.5's core templates already chain the latent
upscaler, so a separate "upscale" template would have duplicated it.

**Can the pinned core even load the model?** A new model family often needs a
new text encoder or ldm module. LTX 2.5 conditions on Gemma 4, which 0.30.0
did not have at all, so no profile or template could have worked.

```bash
ls ComfyUI/comfy/ldm/ | grep -i YOUR_MODEL
grep -rn 'YOUR_ENCODER' ComfyUI/comfy/text_encoders/*.py ComfyUI/comfy/sd.py
```

**Is the repo gated?** `gated: auto` on the Hugging Face API means *approval is
instant*, not that it is skipped — the licence still has to be accepted once by
the token's account. Probe before starting a multi-gigabyte download:

```bash
docker exec -i comfyui python3 -c "
import os, urllib.request, urllib.error
req = urllib.request.Request('https://huggingface.co/ORG/REPO/resolve/main/SMALL_FILE', method='HEAD')
req.add_header('Authorization', f\"Bearer {os.environ.get('HF_TOKEN','')}\")
try: print(urllib.request.urlopen(req, timeout=30).status, 'OK')
except urllib.error.HTTPError as e: print(e.code, e.reason)"
```

403 with a valid token means that specific repo needs its licence accepted.

## 1. Decide how it gets provisioned

Prefer **profile-driven** provisioning for anything you want deterministically
testable — add or extend an entry in `asset-profiles.json`:

```jsonc
"groups": {
  "my-new-model": [
    { "type": "file", "dest": "/workspace/ComfyUI/models/diffusion_models/my_model.safetensors",
      "url": "https://huggingface.co/Org/Repo/resolve/main/my_model.safetensors",
      "label": "My New Model" }
  ]
},
"profiles": {
  "my-new-profile": ["my-new-model"]
}
```

Entry types: `file` (needs `url`), `symlink` (needs `target`, must point at a
dest another group already provisions), `hf_snapshot` (needs `repo_id`, pulls
a whole HF repo directory). Every entry needs a `label`.

If the template belongs to a custom-node pack that ships its own
`example_workflows/`, also check
`custom_node_example_workflow_profiles[module]` so the module scan pulls the
right profile in even with the allowlist alone.

## 2. Build the template with embedded model metadata

Place the workflow JSON under
`custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<Display Name>.json`
(this is the "Ours" surface in `docs/workflows.md`; use it for anything upstream
doesn't already ship a template for).

Every loader node should carry `properties.models`:

```jsonc
"properties": {
  "models": [
    { "name": "my_model.safetensors",
      "url": "https://huggingface.co/Org/Repo/resolve/main/my_model.safetensors",
      "directory": "diffusion_models" }
  ]
}
```

This makes the template self-provisioning through the module scan even
without a profile selected — intentional here, but confirm that's what you
want (a stray HF link anywhere in the file is enough to trigger downloads;
`validate_manifest.py` warns about undeclared self-provisioning).

## 2b. Adapting someone else's workflow

Keep the attribution inside the file, not only in the docs — a `MarkdownNote`
node shows in the GUI and travels with the JSON. State the source workflow,
author, licence, and what you changed and why. If the original is copyleft
(GPL-3.0), say so: the derived file inherits it.

Three things that bite when converting a third-party workflow onto a newer
model:

- **Strip stale repo links from its notes.** A bare Hugging Face repo link
  anywhere in the file lets the asset scanner match loader filenames against
  that repo. Converting a workflow off LTX 2.3 while leaving the original's
  "download the models here" note intact re-provisions the entire 2.3 stack.
- **Declare its node types** in `scripts/smoke/external_node_types.json`, or
  `validate_manifest.py` rejects the template.
- **Architecture changes are not filename swaps.** LTX 2.3 loaded a separate
  text projection through `DualCLIPLoader`; 2.5 folds it into the encoder and
  needs a single `CLIPLoader`. Check the loader's input list, not just the
  filename.

## 3. Add smoke coverage

Add a row to `scripts/smoke/lanes.json`:

```jsonc
["my-new-profile", "/workspace/ComfyUI/custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/My New Workflow.json"]
```

An optional third element is a per-lane model substitution map, for when a
profile shares a template but points at a different checkpoint/LoRA — use
this only for genuine "same template, different weights" cases, never to
paper over a missing model.

Skip the lane (and note why in `scripts/smoke/pending_models.json` if
upstream weights are missing) when the workflow is genuinely
input-dependent (needs a user-supplied file with no bundled sample) or
blocked on unpublished weights.

Also skip it when the graph uses a node whose `widgets_values` cannot be
mapped positionally. `wf_smoke.py` converts UI to API by position, which
breaks on nodes carrying many optional or link-converted widgets — the
`LTXDirector` timeline node has 23 widget values across 11 required and 12
optional inputs and shifts every value after the link-converted ones. Prove
it is the node and not your edit by running the upstream original through
`wf_smoke.py`: if it fails identically, record the row as GUI-only rather
than chasing it. The ComfyUI frontend builds prompts from its own widget
model and is unaffected.

## 4. Validate offline first

```bash
python3 scripts/smoke/validate_manifest.py
```

Fix everything it reports: unknown profile/group references, dangling
symlink targets, node types no installed custom-node pack provides, lane
workflows whose models the profile doesn't actually provision.

## 5. Bring up, provision, and run for real

```bash
docker compose up -d          # recreate if .env changed
docker logs -f comfyui         # watch the download; server starts after it finishes

docker cp scripts/smoke/wf_smoke.py   comfyui:/tmp/wf_smoke.py
docker cp scripts/smoke/run_lanes.py  comfyui:/tmp/run_lanes.py
docker cp scripts/smoke/lanes.json    comfyui:/tmp/lanes.json
docker cp scripts/smoke/audit_refs.py comfyui:/tmp/audit_refs.py

docker exec comfyui python3 -u /tmp/run_lanes.py my-new-profile
docker exec comfyui python3 /tmp/audit_refs.py my-new-profile
```

Both must pass. A passing lane alone is not sufficient proof — `audit_refs.py`
catches models the harness silently stubbed or substituted.

## 6. Look at the output once

Open the produced image/video/audio/text file. A `COMPLETED` smoke result only
proves the graph executed, not that the result is any good.

## 7. Promote it in docs/workflows.md

Move the row from *Provisioned — not yet hardware-verified* into its category
table (or add a new row/table if this is the first of its kind), filling in
the measured Run time from the smoke output, and bump the "All N entries...
verified end to end" count at the top of the file.

If something is genuinely blocked upstream (weights not published), verify
that by searching Hugging Face directly rather than trusting an old note —
availability changes — and leave it (or file it) in
`scripts/smoke/pending_models.json` with a link to watch.
