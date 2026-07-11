#!/bin/bash

VENV_PATH="/workspace/venv"
CONSTRAINTS_FILE="/workspace/constraints.txt"
ASSET_BOOTSTRAP_HELPER="${COMFY_ASSET_BOOTSTRAP_HELPER:-/usr/local/bin/bootstrap_comfy_assets.sh}"
COMFY_PATCH_DIR="${COMFY_PATCH_DIR:-/workspace/patches/comfyui}"

# Create venv if not exists (first run only)
if [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_PATH"
fi

# Ensure all pip operations target the project virtual environment.
source "$VENV_PATH/bin/activate"

require_writable_dir() {
    dir="$1"
    if ! mkdir -p "$dir"; then
        echo "ERROR: cannot create required directory: $dir" >&2
        exit 1
    fi

    probe="$dir/.write-test-$$"
    if ! touch "$probe" 2>/dev/null; then
        echo "ERROR: required path is not writable by $(id -u):$(id -g): $dir" >&2
        exit 1
    fi
    rm -f "$probe"
}

asset_bootstrap_helper() {
    if [ ! -x "$ASSET_BOOTSTRAP_HELPER" ]; then
        echo "ERROR: asset bootstrap helper is missing or not executable: $ASSET_BOOTSTRAP_HELPER" >&2
        return 1
    fi

    "$ASSET_BOOTSTRAP_HELPER" "$@"
}

configure_custom_node_example_workflows() {
    allowlist="$(asset_bootstrap_helper allowlist)"
    if [ -z "$allowlist" ]; then
        return 0
    fi

    export COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST="$allowlist"
    echo "Exposing custom node example workflows for: $allowlist"
}

# Opinionated adjustments to the mounted ComfyUI checkout (example-workflow
# gating pending upstream support, LTX blueprint/profile alignment, bundled
# template smoke harness). Each patch is skipped when already present; a patch
# that no longer fits the checkout only costs its own behavior, never startup.
apply_comfyui_patches() {
    comfy_dir="/workspace/ComfyUI"

    ls "$COMFY_PATCH_DIR"/*.patch >/dev/null 2>&1 || return 0

    if ! command -v patch >/dev/null 2>&1; then
        echo "WARNING: 'patch' is unavailable; skipping ComfyUI startup patches" >&2
        return 0
    fi

    for patch_file in "$COMFY_PATCH_DIR"/*.patch; do
        patch_name="$(basename "$patch_file")"
        if (cd "$comfy_dir" && patch -p1 -N --dry-run < "$patch_file" >/dev/null 2>&1); then
            echo "Applying ComfyUI patch: $patch_name..."
            (cd "$comfy_dir" && patch -p1 -N --no-backup-if-mismatch < "$patch_file")
        elif (cd "$comfy_dir" && patch -p1 -R --dry-run < "$patch_file" >/dev/null 2>&1); then
            : # already applied
        else
            echo "WARNING: ComfyUI patch does not apply to this checkout, skipping: $patch_name" >&2
        fi
    done
}

bootstrap_asset_profiles() {
    asset_bootstrap_helper bootstrap
}

for dir in \
    /workspace/ComfyUI/input \
    /workspace/ComfyUI/input/3d \
    /workspace/ComfyUI/output \
    /workspace/ComfyUI/user; do
    require_writable_dir "$dir"
done

# Update base packages (every run)
echo "Checking/updating base packages..."
export PIP_CONSTRAINT="$CONSTRAINTS_FILE"
python -m pip install --upgrade pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install SageAttention only when it is explicitly enabled in Comfy args.
if [[ " ${COMFY_CMDLINE_EXTRA:-} " == *" --use-sage-attention "* ]]; then
    echo "Sage attention enabled, installing from local pre-built wheel..."
    if ls /opt/sageattention/sageattention-*.whl >/dev/null 2>&1 && \
        python -m pip install --no-deps --force-reinstall /opt/sageattention/sageattention-*.whl; then
        :
    else
        echo "WARNING: Sage attention requested but local wheel is unavailable or invalid; continuing without --use-sage-attention"
        COMFY_CMDLINE_EXTRA="${COMFY_CMDLINE_EXTRA/--use-sage-attention/}"
        COMFY_CMDLINE_EXTRA="$(echo "${COMFY_CMDLINE_EXTRA}" | xargs)"
    fi
fi

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
for src in /opt/flash-attn/*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "flash-attn"
done
for src in /opt/onnxruntime/onnxruntime_gpu-*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "onnxruntime"
done
for src in /opt/decord/*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "decord"
done
for src in /opt/comfy-aimdo/comfy_aimdo-*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "comfy-aimdo"
done
for src in /opt/sageattention/sageattention-*.whl; do
    [ -e "$src" ] || continue
    sync_wheel "$src" "sageattention"
done
# Install FlashAttention from pre-built wheel (built in Docker image for CUDA 13.0)
echo "Installing flash-attn from pre-built wheel..."
python -m pip install /opt/flash-attn/*.whl

# Install decord from pre-built wheel (built in Docker image, no PyPI wheel for Python 3.12)
echo "Installing decord from pre-built wheel..."
python -m pip install /opt/decord/*.whl || true

# Update ComfyUI repository if UPDATE_DEPS is true
if [ "${UPDATE_DEPS}" = "true" ]; then
    echo "Updating ComfyUI repository..."
    cd /workspace/ComfyUI || exit
    git pull
fi

# Install ComfyUI requirements
python -m pip install -r /workspace/ComfyUI/requirements.txt



# Keep the upstream comfy-aimdo version from requirements.txt by default.
# Older local wheels can lag behind ComfyUI's Python package layout.
if [ "${FORCE_LOCAL_COMFY_AIMDO:-false}" = "true" ]; then
    if ls /opt/comfy-aimdo/comfy_aimdo-*.whl >/dev/null 2>&1; then
        echo "Installing comfy-aimdo from pre-built wheel..."
        python -m pip install --no-deps --force-reinstall /opt/comfy-aimdo/comfy_aimdo-*.whl
    else
        echo "WARNING: local comfy-aimdo wheel not found in /opt/comfy-aimdo"
    fi
else
    echo "Keeping comfy-aimdo from ComfyUI requirements.txt"
fi

INSTALL_CUSTOM_NODES=true
if [ -z "${COMFY_NODE_WHITELIST:-}" ] && \
    [ -z "${COMFY_NODE_BLACKLIST:-}" ] && \
    [ "${DISABLE_ALL_CUSTOM_NODES:-true}" = "true" ]; then
    INSTALL_CUSTOM_NODES=false
fi

if [ "${INSTALL_CUSTOM_NODES}" = "true" ]; then
    echo "Bootstrapping custom nodes from custom_nodes.txt"
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
        [ -f "$dir/requirements.txt" ] && python -m pip install -r "$dir/requirements.txt"
    done < /workspace/ComfyUI/custom_nodes/custom_nodes.txt
else
    echo "Skipping custom node clone/install because DISABLE_ALL_CUSTOM_NODES=true"
fi

# Defer ONNX runtime finalization until every base and custom-node dependency
# has finished installing. Some dependency resolution paths reintroduce the
# CPU-only `onnxruntime` package, so the last word needs to be the CUDA wheel.
if ls /opt/onnxruntime/onnxruntime_gpu-*.whl >/dev/null 2>&1; then
    echo "Finalizing onnxruntime-gpu after dependency resolution..."
    python -m pip uninstall -y onnxruntime >/dev/null 2>&1 || true
    python -m pip install --no-deps --force-reinstall /opt/onnxruntime/onnxruntime_gpu-*.whl
else
    echo "WARNING: onnxruntime-gpu wheel not found in /opt/onnxruntime"
fi

# GLSL nodes recommend the optional acceleration extension when available.
echo "Installing PyOpenGL-accelerate..."
python -m pip install PyOpenGL-accelerate || true

apply_comfyui_patches
bootstrap_asset_profiles
configure_custom_node_example_workflows

# Run ComfyUI
COMFY_ARGS=(
  --listen 0.0.0.0
  --port "${COMFY_PORT:-8188}"
)

# Optional extra args for ComfyUI (provided via docker-compose env).
if [ -n "${COMFY_CMDLINE_EXTRA:-}" ]; then
    read -r -a EXTRA_ARGS <<< "${COMFY_CMDLINE_EXTRA}"
    COMFY_ARGS+=("${EXTRA_ARGS[@]}")
fi

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
