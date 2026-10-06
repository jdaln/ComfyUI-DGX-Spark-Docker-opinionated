# Verifying a profile

"The models downloaded" is not the same as "the workflow runs". Every asset
profile has a smoke lane that loads a real workflow, queues it and waits for
output. The harness lives in [`scripts/smoke/`](../scripts/smoke).

| Script | Runs | Needs models | Checks |
| --- | --- | --- | --- |
| `validate_manifest.py` | host, offline | no | manifest structure, dangling symlink targets, node types no installed pack provides, lane to profile mismatches, undeclared self-provisioning |
| `plan_downloads.py [profiles...]` | throwaway container | no | what the profiles would still download, gated repos the token cannot read, and whether the disk keeps a free margin |
| `wf_smoke.py <workflow> <timeout> [subs]` | in container | yes | converts a UI workflow to an API prompt, expanding subgraphs, queues it and waits. The only script that executes a graph |
| `run_lanes.py [profiles...]` | in container | yes | runs every `lanes.json` entry through `wf_smoke.py` one at a time behind a memory gate, writes `/tmp/lane_report.json` |
| `sweep.sh [profiles...]` | host | yes | runs `run_lanes.py` unattended from a detached host process, restarting the container when the memory gate stops it, then the audit and a contact sheet |
| `audit_refs.py [profiles...]` | in container | yes | every model a lane's workflow references resolves on disk |
| `lane_prompt_diff.py <old> <new> [profiles...]` | in container | no | what a change to `wf_smoke.py` does to every lane's prompt, without queueing anything |
| `contact_sheet.py <out> [--report r] [files...]` | in container | no | lane outputs side by side, video as its middle frame |
| `audio_check.py <files...>` | in container | no | duration, level, silence and spectral flatness, to tell music from noise |
| `transcribe_lyrics.py <files...>` | in container | yes | sung lyrics read back out of song outputs with HeartMuLa's transcriber |
| `build_matrix.py` | host and container | yes | cross-checks profile file sets against workflow model references |

## 1. Offline first

```bash
python3 scripts/smoke/validate_manifest.py
```

Runs from the checkout in under a second and needs nothing but the `ComfyUI`
submodule. It catches the mistakes that would otherwise surface only after a
multi-gigabyte download: a profile naming a group that does not exist, a symlink
pointing at a file nothing downloads, a template using a node from a pack
`custom_nodes.txt` never installs, a broken link in a workflow graph, or a lane
whose workflow loads a model its profile does not provision.

It also prints every file the bundled templates provision with no profile
selected, which is the answer to "why is it downloading that".

CI runs it on every push and pull request
([`.github/workflows/validate-manifest.yml`](../.github/workflows/validate-manifest.yml)).

Expect a difference between CI and your machine. CI has only the tracked
template pack in `custom_nodes/`, so it reports only what this repo owns. Once
the container has run, that directory also holds the 34 third-party packs cloned
at startup, and the check audits their example workflows too. Those account for
almost all of the output and are not yours to fix. Filter them out:

```bash
python3 scripts/smoke/validate_manifest.py \
  | grep -Pv 'custom_nodes/(?!ComfyUI-DGX-Spark-Templates)'
```

The exit status still counts everything, so treat the filtered output as the
signal rather than the return code.

Node types that come from a custom node rather than ComfyUI core are declared in
`scripts/smoke/external_node_types.json`, mapped to the pack that provides them,
so a bundled template cannot quietly acquire a dependency the container will not
install. Models a template references on purpose that nothing can provision yet
are declared in `scripts/smoke/pending_models.json` and reported as warnings.

## 2. Against the running container

### Check the download first

The bootstrap does not check free space, and the models usually live on the
system disk. Before adding several profiles to `.env`, ask what they would
fetch:

```bash
docker run --rm --entrypoint python3 --env-file .env \
  -v "$PWD":/repo:ro -w /repo \
  -v "$PWD/../ComfyData/models":/workspace/ComfyUI/models:ro \
  -v "$PWD/../ComfyData/input":/workspace/ComfyUI/input:ro \
  comfyui-dgx-spark-docker-opinionated-comfy \
  scripts/smoke/plan_downloads.py ltx-2.5-a2v talking-characters
```

It lists every file still missing with its size, totals them per profile, names
any gated repository the token cannot read, and exits 1 when the download would
leave less than 40 GB free (`--min-free-gb`). It runs in a throwaway container
with the model folders mounted read-only, so the running one is untouched.

### Run the lanes

`/tmp` is wiped on every container recreate, so stage the harness each time.

```bash
docker compose up -d
docker logs -f comfyui        # the server starts after asset bootstrap finishes

docker cp scripts/smoke/wf_smoke.py   comfyui:/tmp/wf_smoke.py
docker cp scripts/smoke/run_lanes.py  comfyui:/tmp/run_lanes.py
docker cp scripts/smoke/lanes.json    comfyui:/tmp/lanes.json
docker cp scripts/smoke/audit_refs.py comfyui:/tmp/audit_refs.py

# clear stale queued prompts from an earlier run
docker exec comfyui python3 -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8188/queue', data=json.dumps({'clear': True}).encode(), headers={'Content-Type': 'application/json'})
urllib.request.urlopen(req, timeout=30).read()"

# all lanes, or name profiles to run a subset
docker exec comfyui python3 -u /tmp/run_lanes.py
docker exec comfyui python3 -u /tmp/run_lanes.py krea-2-turbo ideogram-4
docker exec comfyui cat /tmp/lane_report.json
```

`run_lanes.py` frees ComfyUI's cached models before each lane and once at the
end, because nothing else does and a batch of large profiles otherwise stacks
them until the machine falls over. It then waits for available memory to come
back above 85 GiB before starting the next lane (`LANE_MIN_AVAILABLE_GIB`), and
stops the run if it has not after 15 minutes (`LANE_GATE_WAIT_S`). After a failed lane it interrupts
the server and clears the queue, so a prompt that outlived its timeout cannot
keep running under the next one. The report records each lane's lowest
available memory and free VRAM. See
[troubleshooting.md](troubleshooting.md#memory-stays-used-after-a-run).

For a long run, such as every lane after a core bump, start it with
`sweep.sh` instead. It refuses to start while ComfyUI is busy and runs as a
detached process on the host, so it carries on after your shell closes. CPU
memory a workflow frees comes back a minute after the queue empties, which the
memory gate's wait allows for. If the gate still stops the run, the sweep
restarts the container and carries on from that lane, up to three times
(`SWEEP_RESTARTS`). It ends with
the audit and a contact sheet:

```bash
scripts/smoke/sweep.sh                     # every lane, or name profiles
scripts/smoke/sweep.sh --status            # progress
ls tmp/sweep/                              # report, audit and sheet.jpg once finished
```

A passing lane is weaker evidence than it looks. The harness can satisfy a
missing model from an inner-node default, a stub input, or a lane-declared
substitution, so a user opening the same workflow could still see a missing
model. Run the audit too:

```bash
docker exec comfyui python3 /tmp/audit_refs.py krea-2-turbo ideogram-4
```

Then open the output file. A `COMPLETED` result only proves the graph executed.

```bash
docker exec comfyui bash -lc "ls -lt /workspace/ComfyUI/output | head"
```

`contact_sheet.py` lays out a report's outputs in one image. For audio,
`audio_check.py` separates music from noise: spectral flatness near 0.5 is
noise, while the music and song outputs here read 1e-3 and below. For songs,
`transcribe_lyrics.py` gives back the sung lyrics; if they match the workflow's,
the vocals are real words. Copy the scripts in like the rest of the harness.

When you change `wf_smoke.py`, check what it does to every lane before running
any of them:

```bash
git show HEAD:scripts/smoke/wf_smoke.py > /tmp/wf_smoke_old.py
docker cp /tmp/wf_smoke_old.py comfyui:/tmp/
docker cp scripts/smoke/lane_prompt_diff.py comfyui:/tmp/
docker exec comfyui python3 /tmp/lane_prompt_diff.py /tmp/wf_smoke_old.py /tmp/wf_smoke.py
```

## 3. Workflows with no lane

Five profiles have no lane, either because the workflow needs a file the repo
does not ship or because its weights are unpublished. Run those directly:

```bash
docker cp "custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<name>.json" comfyui:/tmp/
docker exec comfyui python3 -u /tmp/wf_smoke.py "/tmp/<name>.json" 1800
```

For input-dependent workflows, copy a real file into `ComfyUI/input/` and patch
a scratch copy of the JSON first. See
[troubleshooting.md](troubleshooting.md#workflow-needs-your-own-input).

## 4. Bundled template coverage

Coverage of this repo's own templates is tracked in
`ComfyUI/tests/inference/bundled_template_coverage.json`, installed at startup by
`patches/comfyui/bundled-template-smoke-harness.patch`. It scans the current
`ComfyUI/blueprints` directory at runtime, so a new bundled template cannot
silently fall out of the report.

```bash
docker exec comfyui python /workspace/ComfyUI/tests/inference/run_bundled_template_smokes.py --list
docker exec comfyui python /workspace/ComfyUI/tests/inference/run_bundled_template_smokes.py
```

This runner executes only tracked direct `/prompt` graphs. Blueprints without a
companion API graph stay in the matrix as `todo` or `blocked`.

## Adding a workflow

1. First check whether ComfyUI core already ships a template for the model, and
   whether the pinned core can load it at all. Core shipped templates for both
   MiniMax H3 and LTX 2.5 before this repo did, and LTX 2.5 needs a Gemma 4
   encoder that ComfyUI 0.30.0 did not have. Provision for core's template where
   one exists; bundle your own only for variants it does not cover.
1. Add or extend a profile in `asset-profiles.json`. Entry types are `file`
   (needs `url`), `symlink` (needs `target`, pointing at a dest another group
   provisions) and `hf_snapshot` (needs `repo_id`). Every entry needs a `label`.
2. Put the template JSON in
   `custom_nodes/ComfyUI-DGX-Spark-Templates/example_workflows/<Display Name>.json`
   with `properties.models` on every loader node, giving `name`, `url` and
   `directory`.
3. Add a lane to `scripts/smoke/lanes.json`. Skip this only if the workflow
   genuinely needs a user-supplied file or its weights are unpublished; record
   the latter in `scripts/smoke/pending_models.json`.
4. Run `validate_manifest.py` and fix everything it reports.
5. Run `run_lanes.py` and `audit_refs.py` for the new profile. Both must pass.
6. Open the output file.
7. Move the row into its category table in [workflows.md](workflows.md), fill in
   the measured run time, and correct the counts at the top of that file.

Never use `wf_smoke.py`'s substitution mechanism to cover a real missing model or
a template bug. The substitution map is for genuine "same template, different
weights" cases only.
