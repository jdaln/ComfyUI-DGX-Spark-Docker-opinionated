---
name: comfyui-workflow-integrate
description: 'Integrate or onboard a new workflow (bundled template, blueprint, or custom-node example) into this DGX Spark ComfyUI Docker repo. Use when: adding a new ComfyUI template, wiring a new asset profile, adding smoke-lane coverage for a workflow, or promoting a workflow from "not yet hardware-verified" to verified in WORKFLOWS.md. Covers asset-profiles.json schema, properties.models embedding, lanes.json, and the validate/run/audit toolchain.'
---

# Integrate a New Workflow (this repo)

Background on the provisioning model and the smoke toolchain lives in
[How to test and debug a new workflow.md](../../docs/test_and_debug_new_workflow.md).
This skill is the step-by-step onboarding checklist.

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
(this is the "Ours" surface in `WORKFLOWS.md`; use it for anything upstream
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

## 7. Promote it in WORKFLOWS.md

Move the row from *Provisioned — not yet hardware-verified* into its category
table (or add a new row/table if this is the first of its kind), filling in
the measured Run time from the smoke output, and bump the "All N entries...
verified end to end" count at the top of the file.

If something is genuinely blocked upstream (weights not published), verify
that by searching Hugging Face directly rather than trusting an old note —
availability changes — and leave it (or file it) in
`scripts/smoke/pending_models.json` with a link to watch.
