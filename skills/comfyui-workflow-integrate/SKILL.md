---
name: comfyui-workflow-integrate
description: 'Integrate or onboard a new workflow (bundled template, blueprint, or custom-node example) into this DGX Spark ComfyUI Docker repo. Use when: adding a new ComfyUI template, wiring a new asset profile, adding smoke-lane coverage for a workflow, or promoting a workflow from "not yet hardware-verified" to verified in docs/workflows.md. Covers asset-profiles.json schema, properties.models embedding, lanes.json, and the validate/run/audit toolchain.'
---

# Integrate a New Workflow (this repo)

Background on the provisioning model and the smoke toolchain lives in
[docs/models.md](../../docs/models.md) and
[docs/verifying.md](../../docs/verifying.md).
This skill is the step-by-step onboarding checklist.

## Ground rules

- **Every Python run goes in a container**, the small edit and check scripts
  too: `docker exec comfyui ...` against the server, otherwise a one-off
  locked container (`scripts/tools/locked_run.sh`: no capabilities, read-only
  root, no network unless fetching, read-only mounts, only `-e HF_TOKEN`, never
  `--env-file .env`). Edit files with the editor tools, not host Python.
- **Fetch from containers.** The host resolves the Hub to IPv6 and has no IPv6
  route, so host `curl`/`urllib` time out while containers work.
  `scripts/tools/hf_repo.py` lists a repository (revision, gating, every file
  with size and sha256); `scripts/tools/hf_fetch.py` downloads at a pinned
  revision and keeps a file only after its sha256 matches.
- **Do not work from inside `custom_nodes/`.** Claude Code then creates
  `custom_nodes/.claude/`, and ComfyUI logs an import failure for it on every
  start. Stay at the repo root and use paths.
- **One GPU job at a time**, under the memory guard
  (`scripts/tools/mem_guard.sh`); CPU and GPU share one memory pool here.

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

**Does a newer core ship it, or fix it?** The pinned templates package lags
upstream. YuE2's official templates arrived in a later
`comfyui-workflow-templates` than this checkout pinned and need ComfyUI 0.36;
the `minComfyUIVersion` in a template's index entry says what it wants. Upstream
also fixed MiniMax Music 3 producing noise with CUDA graphs after the old pin.
Compare before bundling or debugging:

```bash
git -C ComfyUI fetch upstream master
git -C ComfyUI diff HEAD upstream/master -- requirements.txt
git -C ComfyUI log --oneline HEAD..upstream/master -- comfy/ldm/YOUR_MODEL comfy/text_encoders comfy_extras
```

If upstream has what you need, rebase `dgx-state` onto upstream master first
(backup branch, then a separate `core: bump comfyui` commit; see
[docs/maintenance.md](../../docs/maintenance.md)) instead of bundling a copy or
cherry-picking.

After any core or torch bump, check the CPU purge. The templates pack's
`cpu_memory.py` calls a function torch does not export (mimalloc's collect, in
`libc10.so`) and reads ComfyUI's prompt queue. Restart and read the log: it
must say `cpu-purge: mimalloc <version> found`, and must not later say
`cannot read the prompt queue`. Then run HeartMuLa once and confirm the
server's `RssAnon` drops back near 2 GiB a minute after it finishes:

```bash
docker logs comfyui 2>&1 | grep cpu-purge
docker exec comfyui sh -c 'grep RssAnon /proc/$(pgrep -f ComfyUI/main.py | head -1)/status'
```

If PyTorch has shipped its release API by then
([#192078](https://github.com/pytorch/pytorch/pull/192078)), switch the module
to it.

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

**What does the repository really hold?** List it before planning around it:
`scripts/tools/hf_repo.py ORG/REPO` prints every file with its size and the
folder totals. Two plan items went wrong on assumptions this would have caught:
a text encoder "the repo already has" was not provisioned anywhere, and a
"47 GB" release turned out to replace one part of a 131 GB base bundle it
depends on (its `release.json` says `"dependency"`). Check memory the same way:
add up what the runtime keeps loaded at once, not the size of one file.

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

Patterns that keep coming back:

- **A smaller quant behind the template's filename.** When Comfy-Org publishes
  a smaller build than the one a core template loads, provision the small file
  and add a `symlink` from the template's filename to it. ComfyUI reads the
  quant format from the file, not the name, so the template runs unchanged on
  the smaller weights; the load log shows the resolved path. Qwen-Image 2.1's
  int8 text encoder name points at the w4a8 build this way. Say so in the docs.
- **Sample inputs.** When a template's `LoadImage` or `LoadAudio` names a file
  from Comfy-Org/workflow_templates' `input/` folder, a `file` entry with its
  raw.githubusercontent.com URL and a `/workspace/ComfyUI/input/` dest makes
  the template runnable unattended (YuE2 cover, Qwen-Image 2.1 edit).
- **Alias profiles.** Lanes are keyed by profile, so several workflows on one
  file set each need their own profile name pointing at the same groups
  (`yue2`, `yue2-cover`, `yue2-bf16`, `yue2-bo8`).
- **Loaders on switched-off branches still count.** Qwen-Image 2.1's prompt
  enhancer only loads with `refine_prompt` on, but its loader sits in the
  graph, so the GUI reports the file missing and `audit_refs.py` fails without
  it. Provision it.
- **A model split over several files.** Put it in its own subfolder
  (`diffusion_models/iris-z-small/`), let the loader's widget name the first
  shard (`iris-z-small/transformer-00001-of-00004.safetensors`) and find the
  rest beside it, and list every shard and side file in `properties.models`
  with the subfolder in `name`. The bootstrap fetches all of a node's entries
  in the same subfolder once the widget names one of them. Only
  `.safetensors`-type files belong there: the frontend reports a listed
  `.json` as missing, so the loader must work without its config file (or the
  profile provisions it).
- **Nodes that fetch their own weights** (Voice Isolator, Separate Two
  Speakers) take no model widget, so `properties.models` cannot drive them:
  pin the revision and check the sha256 in the node, and give the file a
  profile group as well.
- **Changing the bootstrap or the entrypoint.** Both are baked into the image:
  `docker compose build comfy`, then `docker compose up -d --no-deps comfy`.
  Test a bootstrap change offline first by running its spec emitter on the
  template pack with the old and the new script and diffing the two lists.

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
- **Adapt the pack's current example, not the installed copy.** A fresh
  install clones the latest pack, but a pack directory without its own `.git`
  never updates (the entrypoint warns `has no .git of its own`). One such
  ComfyUI-LTXVideo copy had sat two months behind; its HDR example
  predated a rewrite of `LTXVHDRDecodePostprocess`, and its LipDub example had
  become Dub-It upstream. Compare with the upstream repository before copying,
  and if the installed pack is stale, move it aside and clone it again.
- **Check a renamed file before downloading it again.** Hugging Face's `ETag`
  on a Xet-backed file is the Xet hash, not the sha256. The sha256 is
  `lfs.oid` from `POST /api/models/<repo>/paths-info/main`. Lightricks' Dub-It
  LoRA turned out byte-identical to the LipDub file already on disk.
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
paper over a missing model. A LoRA trained on a different kind of guide is not
one: the LTX-2.3 motion-track, HDR and LipDub lanes once ran core's
union-control template with their LoRA swapped in, passed, and produced a copy
of the depth video. Each now runs its own official example.

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

Read the harness log of a new lane before trusting a pass. `using inner node
defaults` means `wf_smoke.py` could not place a subgraph node's own values,
and `defaulted <Node>.prompt = ''` means a prompt went out empty. Both still
pass, on input the workflow never shows a user; before the subgraph and
primitive fixes, the MiniMax Music 3 and YuE2 lanes would have run on empty
text. After changing `wf_smoke.py`, `lane_prompt_diff.py` shows what the change
does to every lane's prompt without queueing anything.

## 4. Validate offline first

```bash
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp \
  --user 1000:1000 -e HOME=/tmp -v "$PWD:/r:ro" --entrypoint sh comfyui-dgx-spark-docker-opinionated-comfy \
  -c 'git config --global --add safe.directory /r; python3 -I /r/scripts/smoke/validate_manifest.py /r'
```

Fix everything it reports: unknown profile/group references, dangling
symlink targets, node types no installed custom-node pack provides, lane
workflows whose models the profile doesn't actually provision. It checks only
the node packs git tracks (or new, unignored files), so the packs the
entrypoint cloned into `custom_nodes/` on this machine do not count; before
that, a working tree reported about 1,600 false problems.

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
catches models the harness silently stubbed or substituted. Grep every lane
log for the harness's stub lines too, all three: `fed unconnected` (an
IMAGE/VIDEO/AUDIO input with no link got a sample file; a misnamed input in a
builder once fed a refine the wrong video, and the lane still passed),
`defaulted` and `using inner node defaults`.

When you run a sweep, write its lane list (or "every lane") into the plan: an
earlier 50-lane selection was lost and could not be repeated.

For more than a couple of lanes, or anything left running unattended, use
`scripts/smoke/sweep.sh <profiles>`. It runs them one at a time behind
`run_lanes.py`'s memory gate, restarts the container when something holds memory
`/free` cannot return, and leaves the report, the audit and a contact sheet in
`tmp/sweep/`.

## 6. Look at the output once

Open the produced image/video/audio/text file. A `COMPLETED` smoke result only
proves the graph executed, not that the result is any good. Compare it with
the prompt the lane actually sent, not the one you expect.

- `contact_sheet.py` puts images and video frames side by side.
- `audio_check.py` separates music from noise: spectral flatness near 0.5 is
  noise, the music outputs here read 1e-3 and below.
- `transcribe_lyrics.py` reads sung lyrics back out with HeartMuLa's
  transcriber. Getting the workflow's lyrics back shows the vocals are words.

## 7. Promote it in docs/workflows.md

Move the row from *Provisioned — not yet hardware-verified* into its category
table (or add a new row/table if this is the first of its kind), filling in
the measured Run time from the smoke output. A lane's time includes the first
model load (Iris-Z: 146 s cold, 10 s a photo once loaded); where the two
differ a lot, give both in the note or the paragraph. Then update the counts in the
opening paragraph ("defines N profiles. The M in the category tables") and the
"Most of those M" line under it; `validate_manifest.py` checks the first two.
README.md and docs/models.md state the profile and template counts as well.

If something is genuinely blocked upstream (weights not published), verify
that by searching Hugging Face directly rather than trusting an old note —
availability changes — and leave it (or file it) in
`scripts/smoke/pending_models.json` with a link to watch.
