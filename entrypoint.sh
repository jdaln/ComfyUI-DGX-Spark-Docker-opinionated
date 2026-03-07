#!/bin/bash

VENV_PATH="/workspace/venv"
CONSTRAINTS_FILE="/workspace/constraints.txt"

# Create venv if not exists (first run only)
if [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_PATH"
fi

# Update base packages (every run)
echo "Checking/updating base packages..."
export PIP_CONSTRAINT="$CONSTRAINTS_FILE"
pip install --upgrade pip
pip install torch torchvision torchaudio --pre --index-url https://download.pytorch.org/whl/cu130
pip install sageattention || true

# Backup built wheels into mounted directory (update if missing or different)
WHEELS_BACKUP_DIR="/workspace/SelfBuiltWheels"
if ! mkdir -p "$WHEELS_BACKUP_DIR"; then
    echo "ERROR: cannot create backup dir: $WHEELS_BACKUP_DIR" >&2
fi
sync_wheel() {
    src="$1"
    subdir="$2"
    [ -f "$src" ] || return
    dst_dir="$WHEELS_BACKUP_DIR/$subdir"
    if ! mkdir -p "$dst_dir"; then
        echo "ERROR: cannot create dir: $dst_dir" >&2
        return 1
    fi
    dst="$dst_dir/$(basename "$src")"
    if [ ! -f "$dst" ] || ! cmp -s "$src" "$dst"; then
        echo "Backing up wheel: $(basename "$src")"
        if ! cp -f "$src" "$dst"; then
            echo "ERROR: cannot copy $src to $dst" >&2
            return 1
        fi
    fi
}
for src in /opt/flash-attn3/*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "flash-attn3"
done
for src in /opt/onnxruntime/onnxruntime_gpu-*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "onnxruntime"
done
for src in /opt/decord/*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "decord"
done

# Install FlashAttention-3 from pre-built wheel (built in Docker image for CUDA 13.0)
echo "Installing flash-attn3 from pre-built wheel..."
pip install /opt/flash-attn3/*.whl

# Install onnxruntime-gpu from pre-built wheel (built in Docker image for CUDA 13.0)
echo "Installing onnxruntime-gpu from pre-built wheel..."
pip install /opt/onnxruntime/onnxruntime_gpu-*.whl

# Install decord from pre-built wheel (built in Docker image, no PyPI wheel for Python 3.12)
echo "Installing decord from pre-built wheel..."
pip install /opt/decord/*.whl || true

# Update ComfyUI repository if UPDATE_DEPS is true
if [ "${UPDATE_DEPS}" = "true" ]; then
    echo "Updating ComfyUI repository..."
    cd /workspace/ComfyUI || exit
    git pull
fi

# Install ComfyUI requirements
pip install -r /workspace/ComfyUI/requirements.txt

# Clone custom nodes from list
cd /workspace/ComfyUI/custom_nodes || exit
while IFS= read -r repo || [ -n "$repo" ]; do
    [[ -z "$repo" || "$repo" =~ ^# ]] && continue
    dir=$(basename "$repo" .git)
    [ -d "$dir" ] || git clone "$repo"
    # Update existing custom node if UPDATE_DEPS is true
    if [ "${UPDATE_DEPS}" = "true" ] && [ -d "$dir" ]; then
        echo "Updating custom node: $dir"
        cd "$dir" || exit
        git pull
        cd ..
    fi
    [ -f "$dir/requirements.txt" ] && pip install -r "$dir/requirements.txt"
done < /workspace/ComfyUI/custom_nodes/custom_nodes.txt

# Run ComfyUI
COMFY_ARGS=(
  --listen 0.0.0.0
  --port "${COMFY_PORT:-8188}"
)

# Debug mode for custom node bisection:
# - If COMFY_NODE_WHITELIST is set (comma-separated), load only these nodes.
# - Else if COMFY_NODE_BLACKLIST is set (comma-separated), load all node dirs except blacklisted.
# - Else DISABLE_ALL_CUSTOM_NODES=true keeps custom nodes disabled.
if [ -n "${COMFY_NODE_WHITELIST:-}" ]; then
    COMFY_ARGS+=(--disable-all-custom-nodes)
    IFS=',' read -ra NODE_LIST <<< "${COMFY_NODE_WHITELIST}"
    WHITELIST_NODES=()
    for node in "${NODE_LIST[@]}"; do
        node_trimmed="$(echo "$node" | xargs)"
        [ -n "$node_trimmed" ] && WHITELIST_NODES+=("$node_trimmed")
    done
    [ "${#WHITELIST_NODES[@]}" -gt 0 ] && COMFY_ARGS+=(--whitelist-custom-nodes "${WHITELIST_NODES[@]}")
elif [ -n "${COMFY_NODE_BLACKLIST:-}" ]; then
    COMFY_ARGS+=(--disable-all-custom-nodes)

    # Build a hash-set once; membership checks are O(1) per node.
    IFS=',' read -ra BLACKLIST_NODES <<< "${COMFY_NODE_BLACKLIST}"
    declare -A BLACKLIST_SET=()
    for raw in "${BLACKLIST_NODES[@]}"; do
        node_trimmed="${raw#"${raw%%[![:space:]]*}"}"
        node_trimmed="${node_trimmed%"${node_trimmed##*[![:space:]]}"}"
        [ -n "$node_trimmed" ] && BLACKLIST_SET["$node_trimmed"]=1
    done

    WHITELIST_NODES=()
    for node_path in /workspace/ComfyUI/custom_nodes/*; do
        [ -d "$node_path" ] || continue
        node_name="$(basename "$node_path")"
        [ "$node_name" = "__pycache__" ] && continue
        [ -n "${BLACKLIST_SET[$node_name]:-}" ] && continue
        WHITELIST_NODES+=("$node_name")
    done
    [ "${#WHITELIST_NODES[@]}" -gt 0 ] && COMFY_ARGS+=(--whitelist-custom-nodes "${WHITELIST_NODES[@]}")
elif [ "${DISABLE_ALL_CUSTOM_NODES:-true}" = "true" ]; then
    COMFY_ARGS+=(--disable-all-custom-nodes)
fi

python /workspace/ComfyUI/main.py "${COMFY_ARGS[@]}"
