#!/usr/bin/env bash
# Run one command in a locked-down one-off container from the comfy image, under the memory guard.
#
#   scripts/tools/locked_run.sh [options] NAME -- COMMAND [ARGS...]
#
# Options:
#   --gpu            give the container the GPU
#   --net            allow network (only for fetching; tests and model runs go without)
#   --floor GIB      memory floor for the guard (default 8)
#   --rw HOST:CTR    writable mount (repeatable); keep these to an output folder
#   --ro HOST:CTR    read-only mount (repeatable)
#   --env NAME       pass one variable from this shell (repeatable), e.g. --env HF_TOKEN; never --env-file .env
#
# Always: --cap-drop ALL, no-new-privileges, read-only root, tmpfs /tmp, user 1000:1000, HOME=/tmp, no .pyc
# writes. ComfyUI, the venv and the models are not mounted unless asked for with --ro, for example
#   --ro "$PWD/ComfyUI:/workspace/ComfyUI" --ro "$PWD/venv:/workspace/venv" \
#   --ro "$COMFY_DATA/models:/workspace/ComfyUI/models"     (COMFY_DATA: the data folder docker-compose mounts)
# and COMMAND /workspace/venv/bin/python ... for the repo's Python. The guard's log goes to tmp/guard-NAME.log.
set -uo pipefail
gpu=() net=(--network none) floor=8 mounts=() envs=()
while [ $# -gt 0 ]; do
    case "$1" in
        --gpu) gpu=(--gpus all); shift ;;
        --net) net=(); shift ;;
        --floor) floor=$2; shift 2 ;;
        --rw) mounts+=(-v "$2"); shift 2 ;;
        --ro) mounts+=(-v "$2:ro"); shift 2 ;;
        --env) envs+=(-e "$2"); shift 2 ;;
        --) shift; break ;;
        *) name=$1; shift ;;
    esac
done
[ -n "${name:-}" ] && [ $# -gt 0 ] || { echo "usage: $0 [options] NAME -- COMMAND..." >&2; exit 2; }
repo="$(cd "$(dirname "$0")/../.." && pwd)"
docker rm "$name" >/dev/null 2>&1
docker run -d --name "$name" "${gpu[@]}" "${net[@]}" --user 1000:1000 --cap-drop ALL \
    --security-opt no-new-privileges --read-only --tmpfs /tmp -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    "${envs[@]}" "${mounts[@]}" --entrypoint "$1" comfyui-dgx-spark-docker-opinionated-comfy "${@:2}" >/dev/null || exit 1
mkdir -p "$repo/tmp"
bash "$repo/scripts/tools/mem_guard.sh" "$name" "$floor" > "$repo/tmp/guard-$name.log" 2>&1 &
code=$(docker wait "$name")
docker logs "$name" 2>&1 | tr '\r' '\n' | tail -"${TAIL:-20}"
sleep 2
tail -1 "$repo/tmp/guard-$name.log"
docker rm "$name" >/dev/null 2>&1
exit "$code"
