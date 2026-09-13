# Configuration

Everything is driven by `.env` in the repo root. `docker-compose.yml` loads it
with `env_file`, so the file must exist; copy `.env.example` to `.env` before
the first start.

After editing `.env`, run `docker compose up -d`. That recreates the container
with the new values. `docker compose restart` keeps the environment baked into
the existing container and is a common source of "my change did nothing".

## Environment variables

| Variable | What it does | Default |
| --- | --- | --- |
| `UID` | User id the container runs as. Set to `id -u`. | none, required |
| `GID` | Group id the container runs as. Set to `id -g`. | none, required |
| `COMFY_PORT` | Port ComfyUI listens on, inside and outside. | `8188` |
| `COMFY_HOST_BIND` | Host address the port is published on. `0.0.0.0` exposes it on the network. | `127.0.0.1` |
| `COMFY_SHM_SIZE` | Size of the container's private `/dev/shm`. | `16g` |
| `COMFY_CMDLINE_EXTRA` | Extra arguments appended to `main.py`. | `--bf16-unet --bf16-vae --bf16-text-enc --use-sage-attention` |
| `COMFY_IDLE_UNLOAD_MINUTES` | Unload cached models after this many minutes with no activity. `0` disables. | `60` |
| `COMFY_IDLE_UNLOAD_POLL_SECONDS` | How often the idle watcher checks. | `60` |
| `UPDATE_DEPS` | `git pull` ComfyUI and every custom node on each start. | `false`, set to `true` in `.env.example` |
| `COMFY_ASSET_PROFILES` | Comma-separated asset profiles to download. See [models.md](models.md). | empty |
| `HF_TOKEN` | Hugging Face token, required for gated models. | empty |
| `DISABLE_ALL_CUSTOM_NODES` | Load no custom nodes, when neither list below is set. | `true`, set to `false` in `.env.example` |
| `COMFY_NODE_WHITELIST` | Load only these custom node directories. | empty |
| `COMFY_NODE_BLACKLIST` | Load every custom node directory except these. | empty |
| `COMFY_CUSTOM_NODE_MODULES_ALLOWLIST` | Gate for third-party example workflows. Any value other than `false`, `0`, `no`, `off` or empty means on. | `true` |
| `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` | Node directories whose example workflows stay visible and get their assets downloaded. | `ComfyUI-WanVideoWrapper,ComfyUI-KJNodes,ComfyUI-WanAnimatePreprocess,ComfyUI-qwenmultiangle,ComfyUI-DGX-Spark-Templates` |
| `COMFY_ASSET_MANIFEST_PATH` | Path to the profile manifest inside the container. | `/workspace/asset-profiles.json` |
| `WAN_PREPROCESS_VITPOSE_URL` | Override the download URL for `vitpose-l-wholebody.onnx`. | built in |
| `WAN_PREPROCESS_YOLO_URL` | Override the download URL for `yolov10m.onnx`. | built in |
| `FORCE_LOCAL_COMFY_AIMDO` | Install the committed `comfy-aimdo` wheel instead of the version in ComfyUI's `requirements.txt`. Older local wheels lag behind ComfyUI's package layout. | `false` |

`UPDATE_DEPS` updates the code inside the container. It does not update this
repo; pull that yourself with `git pull`.

## Volumes

| Container path | Host path | What it holds |
| --- | --- | --- |
| `/workspace/venv` | `./venv` | Python virtual environment |
| `/workspace/pip_cache` | `./pip_cache` | pip cache |
| `/workspace/cache/huggingface` | `./hf_cache` | Hugging Face cache |
| `/workspace/cache/ultralytics` | `./ultralytics_cache` | YOLO cache |
| `/workspace/ComfyUI` | `./ComfyUI` | ComfyUI source, the submodule |
| `/workspace/ComfyUI/custom_nodes` | `./custom_nodes` | cloned custom node packs |
| `/workspace/SelfBuiltWheels` | `./SelfBuiltWheels` | wheels the container copies out of the image |
| `/workspace/ComfyUI/models` | `../ComfyData/models` | models |
| `/workspace/ComfyUI/user` | `../ComfyData/user` | saved workflows and settings |
| `/workspace/ComfyUI/input` | `../ComfyData/input` | input files |
| `/workspace/ComfyUI/output` | `../ComfyData/output` | generated files |

The last four sit one level above the repository on purpose. Models and outputs
are expensive to regenerate and must survive a `git clean -xfd` in the checkout.
Keep them outside.

Everything under the repo itself is disposable. To reset the Python environment:

```bash
docker compose down
rm -rf venv/* pip_cache/*
docker compose up -d
```

## Custom nodes

The list of repositories to clone lives in
[`custom_nodes/custom_nodes.txt`](../custom_nodes/custom_nodes.txt), one git URL
per line. Add a line to install a pack on the next start. Comment it out with
`#` to stop cloning it; the directory that is already there is not deleted.

Which of the cloned packs actually load is decided by three variables, in this
order:

| Priority | Variable | Effect |
| --- | --- | --- |
| 1 | `COMFY_NODE_WHITELIST` | only the listed directories load |
| 2 | `COMFY_NODE_BLACKLIST` | every directory except the listed ones loads |
| 3 | `DISABLE_ALL_CUSTOM_NODES=true` | nothing loads |

Names are matched exactly against directory names under `custom_nodes/`, not
against repository or package names. `ls custom_nodes/` is the authoritative
list. A name that matches no directory has no effect; startup warns about it.

```dotenv
DISABLE_ALL_CUSTOM_NODES=false
COMFY_NODE_BLACKLIST=ComfyUI-SAM3-DGX-Spark
```

Use the blacklist to bisect a pack that breaks a workflow. See
[troubleshooting.md](troubleshooting.md).

## Third-party example workflows

Custom node packs ship their own example workflows. Showing all of them clutters
the template browser and, because the container downloads the assets those
examples reference, costs disk.

- `COMFY_CUSTOM_NODE_MODULES_ALLOWLIST` turns the filtering on and off. It is a
  gate, not a list.
- `COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST` is the list of node directory
  names that stay visible. The default is five packs, including this repo's own
  `ComfyUI-DGX-Spark-Templates`.

Narrow the set:

```dotenv
COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST=ComfyUI-WanVideoWrapper,ComfyUI-KJNodes
```

Hide every third-party example workflow:

```dotenv
COMFY_CUSTOM_NODE_MODULES_ALLOWLIST=false
```

Upstream ComfyUI does not read either variable. The entrypoint applies
`patches/comfyui/custom-node-example-workflow-gating.patch` to the mounted
checkout at startup to add the behaviour. If that patch stops applying, every
installed pack's example workflows become visible again and asset bootstrap
still works. See [maintenance.md](maintenance.md).
