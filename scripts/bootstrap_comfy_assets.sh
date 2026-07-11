#!/bin/bash
set -euo pipefail

ASSET_MANIFEST_PATH="${COMFY_ASSET_MANIFEST_PATH:-/workspace/asset-profiles.json}"

ensure_optional_writable_dir() {
    dir="$1"
    if ! mkdir -p "$dir"; then
        echo "WARNING: cannot create optional directory: $dir" >&2
        return 1
    fi

    probe="$dir/.write-test-$$"
    if ! touch "$probe" 2>/dev/null; then
        echo "WARNING: optional path is not writable by $(id -u):$(id -g): $dir" >&2
        return 1
    fi
    rm -f "$probe"
}

download_if_missing() {
    dest="$1"
    url="$2"
    label="$3"

    if [ -f "$dest" ]; then
        return 0
    fi

    tmp="${dest}.part"
    mkdir -p "$(dirname "$dest")"

    format_bytes() {
        bytes="$1"
        if command -v numfmt >/dev/null 2>&1; then
            numfmt --to=iec-i --suffix=B "$bytes"
        else
            printf '%sB' "$bytes"
        fi
    }

    log_progress() {
        if [ ! -f "$tmp" ]; then
            return 0
        fi

        size=$(stat -c %s "$tmp" 2>/dev/null || echo 0)
        echo "Download progress for $label: $(format_bytes "$size") written"
    }

    curl_args=(
        --fail
        --silent
        --show-error
        --location
        --retry 5
        --retry-all-errors
        --retry-delay 2
        --speed-limit 1024
        --speed-time 120
        -o "$tmp"
    )

    curl_config=""
    if [ -f "$tmp" ]; then
        echo "Resuming $label..."
        curl_args+=(--continue-at -)
    else
        echo "Downloading $label..."
    fi

    if [[ "$url" == https://huggingface.co/* ]] && [ -n "${HF_TOKEN:-}" ]; then
        curl_config="$(mktemp)"
        chmod 600 "$curl_config"
        printf '%s\n' "header = \"Authorization: Bearer ${HF_TOKEN}\"" > "$curl_config"
        curl_args+=(--config "$curl_config")
    fi

    curl "${curl_args[@]}" "$url" &
    curl_pid=$!
    progress_interval="${DOWNLOAD_STATUS_INTERVAL_SECONDS:-20}"

    while kill -0 "$curl_pid" 2>/dev/null; do
        sleep "$progress_interval" &
        wait "$!" || true
        kill -0 "$curl_pid" 2>/dev/null || break
        log_progress
    done

    if wait "$curl_pid"; then
        [ -n "$curl_config" ] && rm -f "$curl_config"
        log_progress
        mv "$tmp" "$dest"
        echo "Finished $label: $(format_bytes "$(stat -c %s "$dest" 2>/dev/null || echo 0)")"
        return 0
    fi

    [ -n "$curl_config" ] && rm -f "$curl_config"
    log_progress || true
    echo "WARNING: failed to download $label from $url" >&2
    if [[ "$url" == https://huggingface.co/* ]]; then
        if [ -n "${HF_TOKEN:-}" ]; then
            echo "WARNING: Hugging Face access may be gated; confirm the HF_TOKEN account has accepted access for this asset." >&2
        else
            echo "WARNING: Hugging Face access may be gated; set HF_TOKEN in the container environment to download gated assets." >&2
        fi
    fi
    echo "WARNING: leaving partial download in place for $label so the next bootstrap can resume." >&2
    return 1
}

download_hf_snapshot_if_missing() {
    dest_dir="$1"
    repo_id="$2"
    label="$3"
    complete_marker="$dest_dir/.asset-profile-complete"

    if [ -f "$complete_marker" ]; then
        return 0
    fi

    tmp_dir="${dest_dir}.part"
    rm -rf "$tmp_dir"
    mkdir -p "$tmp_dir"
    echo "Downloading $label..."
    if python - "$repo_id" "$tmp_dir" <<'PY'
from huggingface_hub import snapshot_download
import os
import sys

repo_id = sys.argv[1]
dest_dir = sys.argv[2]

try:
    snapshot_download(repo_id=repo_id, local_dir=dest_dir, token=os.environ.get("HF_TOKEN") or None)
except Exception as exc:
    message = f"{type(exc).__name__}: {exc}"
    if "401" in message or "403" in message or "GatedRepoError" in message:
        if os.environ.get("HF_TOKEN"):
            print(
                f"WARNING: Hugging Face access denied for {repo_id}; confirm the HF_TOKEN account has accepted access.",
                file=sys.stderr,
            )
        else:
            print(
                f"WARNING: Hugging Face auth required for {repo_id}; set HF_TOKEN in the container environment.",
                file=sys.stderr,
            )
        raise SystemExit(1)
    raise
PY
    then
        rm -rf "$dest_dir"
        mv "$tmp_dir" "$dest_dir"
        : > "$complete_marker"
        echo "Finished $label"
        return 0
    fi

    echo "WARNING: failed to download $label from $repo_id" >&2
    rm -rf "$tmp_dir"
    return 1
}

create_symlink_if_missing() {
    dest="$1"
    target="$2"
    label="$3"

    if [ -L "$dest" ] || [ -e "$dest" ]; then
        return 0
    fi

    mkdir -p "$(dirname "$dest")"
    echo "Linking $label..."
    if ln -s "$target" "$dest"; then
        return 0
    fi

    echo "WARNING: failed to link $label from $target" >&2
    rm -f "$dest"
    return 1
}

emit_requested_asset_specs() {
    local allowlist
    allowlist="${COMFY_EFFECTIVE_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST:-$(emit_custom_node_example_workflow_allowlist)}"

    COMFY_EFFECTIVE_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST="$allowlist" python - "$ASSET_MANIFEST_PATH" <<'PY'
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from huggingface_hub import HfApi
except Exception:
    HfApi = None

manifest_path = sys.argv[1]
profiles = [profile.strip() for profile in os.environ.get("COMFY_ASSET_PROFILES", "").split(",") if profile.strip()]
effective_modules = [
    item.strip()
    for item in os.environ.get("COMFY_EFFECTIVE_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST", "").split(",")
    if item.strip()
]

manifest = {}
if os.path.exists(manifest_path):
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

module_profile_map = manifest.get("custom_node_example_workflow_profiles", {})
for module in effective_modules:
    profiles.extend(module_profile_map.get(module, []))

if not profiles and not effective_modules:
    raise SystemExit(0)

MODEL_EXTENSIONS = (".safetensors", ".onnx", ".bin", ".gguf", ".pth")
WORKFLOW_DIRS = ("example_workflows", "example", "examples", "workflow", "workflows")
DIRECTORY_ROOTS = {
    "UNETLoader": "diffusion_models",
    "DiffusionModelLoaderKJ": "diffusion_models",
    "WanVideoModelLoader": "diffusion_models",
    "WanVideoVACEModelSelect": "diffusion_models",
    "WanVideoExtraModelSelect": "diffusion_models",
    "FantasyPortraitModelLoader": "diffusion_models",
    "FantasyTalkingModelLoader": "diffusion_models",
    "MultiTalkModelLoader": "diffusion_models",
    "LoadLynxResampler": "diffusion_models",
    "VAELoader": "vae",
    "WanVideoVAELoader": "vae",
    "LoadVQVAE": "vae",
    "WanVideoTinyVAELoader": "vae_approx",
    "CLIPLoader": "text_encoders",
    "DualCLIPLoader": "text_encoders",
    "LoadWanVideoT5TextEncoder": "text_encoders",
    "WanVideoTextEncodeCached": "text_encoders",
    "CLIPVisionLoader": "clip_vision",
    "LoadWanVideoClipTextEncoder": "clip_vision",
    "OnnxDetectionModelLoader": "detection",
    "WanVideoControlnetLoader": "controlnet",
    "Wav2VecModelLoader": "wav2vec2",
    "WhisperModelLoader": "audio_encoders",
    "MelBandRoFormerModelLoader": "audio_encoders",
    "OviMMAudioVAELoader": "mmaudio",
    "WanVideoLoraSelect": "loras",
    "WanVideoLoraSelectMulti": "loras",
    "LoraLoaderModelOnly": "loras",
}
STRIPPED_PATH_PREFIXES = {"wanvideo", "wanvid"}
MD_LINK_RE = re.compile(r"\((https://huggingface\.co/[^)\s]+)\)")
PLAIN_URL_RE = re.compile(r"https://huggingface\.co/[^\s)\]]+")
GENERIC_HF_BASENAMES = {"diffusion_pytorch_model.safetensors", "main", "none"}
SOURCE_OVERRIDES = {
    "WanVideo/2_2/Wan2_2-Animate-14B_high_fp8_e4m3fn_native_KJ.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/Wan2_2-Animate-14B_fp8_scaled_e4m3fn_KJ_v2.safetensors",
    "Wan2_2-Animate-14B_high_fp8_e4m3fn_native_KJ.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/Wan2_2-Animate-14B_fp8_scaled_e4m3fn_KJ_v2.safetensors",
    "LongCat/LongCat-Avatar_bf16.safetensors": "https://huggingface.co/Kijai/LongCat-Video_comfy/resolve/main/Avatar/LongCat-Avatar_comfy_bf16.safetensors",
    "LongCat-Avatar_bf16.safetensors": "https://huggingface.co/Kijai/LongCat-Video_comfy/resolve/main/Avatar/LongCat-Avatar_comfy_bf16.safetensors",
    "LongCat_distill_lora_rank128_bf16.safetensors": "https://huggingface.co/Kijai/LongCat-Video_comfy/resolve/main/LongCat_distill_lora_alpha64_bf16.safetensors",
    "WanVideo/Ovi/Wan_2_1_Ovi_audio_model_bf16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Ovi/Wan_2_2_Ovi_audio_model_bf16.safetensors",
    "Wan_2_1_Ovi_audio_model_bf16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Ovi/Wan_2_2_Ovi_audio_model_bf16.safetensors",
    "WanVideo/Ovi/Wan_2_1_Ovi_video_model_bf16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Ovi/Wan_2_2_Ovi_video_model_bf16.safetensors",
    "Wan_2_1_Ovi_video_model_bf16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Ovi/Wan_2_2_Ovi_video_model_bf16.safetensors",
    "WanVideo/Ovi/model_960x960_10s.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/TI2V/Ovi/Wan2_2-5B-Ovi_960x960_10s_fp8_e4m3fn_scaled_KJ.safetensors",
    "model_960x960_10s.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/TI2V/Ovi/Wan2_2-5B-Ovi_960x960_10s_fp8_e4m3fn_scaled_KJ.safetensors",
    "WanVideo/2_2/Wan2_2-T2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
    "Wan2_2-T2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
    "WanVideo/2_2/wan2.2_ti2v_5B_fp16.safetensors": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
    "wan2.2_ti2v_5B_fp16.safetensors": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
    "WanVideo/SkyreelsV3/Wan21_SkyReelsV3-A2V_fp8_scaled_mixed.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SkyReelsV3/Wan21-SkyReelsV3-A2V_fp8_scaled_mixed.safetensors",
    "Wan21_SkyReelsV3-A2V_fp8_scaled_mixed.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SkyReelsV3/Wan21-SkyReelsV3-A2V_fp8_scaled_mixed.safetensors",
    "WanVideo/Wan2_1_SkyreelsA2_fp8_e4m3fn.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Skyreels/Wan2_1_SkyreelsA2_fp8_e4m3fn.safetensors",
    "Wan2_1_SkyreelsA2_fp8_e4m3fn.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Skyreels/Wan2_1_SkyreelsA2_fp8_e4m3fn.safetensors",
    "WanVideo/Stand-In/Stand-In_wan2.1_T2V_14B_ver1.0.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Stand-In/Stand-In_wan2.1_T2V_14B_ver1.0_fp16.safetensors",
    "Stand-In_wan2.1_T2V_14B_ver1.0.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Stand-In/Stand-In_wan2.1_T2V_14B_ver1.0_fp16.safetensors",
    "WanVideo/SteadyDancer/Wan2.1-SteadyDancer_fp8_scaled_KJ.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SteadyDancer/Wan21_SteadyDancer_fp8_e4m3fn_scaled_KJ.safetensors",
    "Wan2.1-SteadyDancer_fp8_scaled_KJ.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/SteadyDancer/Wan21_SteadyDancer_fp8_e4m3fn_scaled_KJ.safetensors",
    "WanVideo/Wan2_1_self_forcing_dmd_1_3B_lora_rank_32_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan2_1_self_forcing_1_3B/Wan2_1_self_forcing_dmd_1_3B_lora_rank_32_fp16.safetensors",
    "Wan2_1_self_forcing_dmd_1_3B_lora_rank_32_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan2_1_self_forcing_1_3B/Wan2_1_self_forcing_dmd_1_3B_lora_rank_32_fp16.safetensors",
    "WanVideo/WanAnimate_relight_lora_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors",
    "WanAnimate_relight_lora_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors",
    "WanVideo/Wan2.1-Fun-V1.1-1.3B-Control-Camera.safetensors": "https://huggingface.co/alibaba-pai/Wan2.1-Fun-V1.1-1.3B-Control-Camera/resolve/main/diffusion_pytorch_model.safetensors",
    "Wan2.1-Fun-V1.1-1.3B-Control-Camera.safetensors": "https://huggingface.co/alibaba-pai/Wan2.1-Fun-V1.1-1.3B-Control-Camera/resolve/main/diffusion_pytorch_model.safetensors",
    "WanVid/wan2.1-1.3b-control-lora-tile-v0.1_comfy.safetensors": "https://huggingface.co/spacepxl/Wan2.1-control-loras/resolve/main/1.3b/tile/wan2.1-1.3b-control-lora-tile-v0.2_comfy.safetensors",
    "wan2.1-1.3b-control-lora-tile-v0.1_comfy.safetensors": "https://huggingface.co/spacepxl/Wan2.1-control-loras/resolve/main/1.3b/tile/wan2.1-1.3b-control-lora-tile-v0.2_comfy.safetensors",
    "WanVideo/Wan2_1-Wan-I2V-ATI-14B_fp8_e4m3fn.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1-I2V-ATI-14B_fp8_e4m3fn.safetensors",
    "Wan2_1-Wan-I2V-ATI-14B_fp8_e4m3fn.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1-I2V-ATI-14B_fp8_e4m3fn.safetensors",
    "WanVideo/lynx/Wan2_1-T2V-Lynx_full_ref_layers_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lynx/Wan2_1-T2V-14B-Lynx_full_ref_layers_fp16.safetensors",
    "Wan2_1-T2V-Lynx_full_ref_layers_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lynx/Wan2_1-T2V-14B-Lynx_full_ref_layers_fp16.safetensors",
    "WanVideo/lynx/Wan2_1-T2V-Lynx_lite_ip_layers_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lynx/Wan2_1-T2V-14B-Lynx_lite_ip_layers_fp16.safetensors",
    "Wan2_1-T2V-Lynx_lite_ip_layers_fp16.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lynx/Wan2_1-T2V-14B-Lynx_lite_ip_layers_fp16.safetensors",
    "WanVideo/Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank32_bf16_.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank32_bf16.safetensors",
    "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank32_bf16_.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank32_bf16.safetensors",
    "WanVideo/Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16_.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors",
    "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16_.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors",
    "lightx2v_I2V_not_clamped_rank_64_fp16_00001_.safetensors": "https://huggingface.co/lgylgy/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64/resolve/main/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors",
    "t5/t5xxl_fp8_e4m3fn_scaled.safetensors": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "t5xxl_fp8_e4m3fn_scaled.safetensors": "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "wan2.2-ti2v-5b-controlnet-depth-v1/diffusion_pytorch_model.safetensors": "https://huggingface.co/TheDenk/wan2.2-ti2v-5b-controlnet-depth-v1/resolve/main/diffusion_pytorch_model.safetensors",
    "wanvideo/MTV_Crafter_4DMoT_VQVAE_fp32.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/MTVCrafter/WanVideo_MTV_Crafter_4DMoT_VQVAE_fp32.safetensors",
    "MTV_Crafter_4DMoT_VQVAE_fp32.safetensors": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/MTVCrafter/WanVideo_MTV_Crafter_4DMoT_VQVAE_fp32.safetensors",
}

groups = manifest.get("groups", {})
profile_map = manifest.get("profiles", {})
url_overrides = {
    "/workspace/ComfyUI/models/detection/vitpose-l-wholebody.onnx": os.environ.get("WAN_PREPROCESS_VITPOSE_URL", ""),
    "/workspace/ComfyUI/models/detection/onnx/yolov10m.onnx": os.environ.get("WAN_PREPROCESS_YOLO_URL", ""),
}
seen_profiles = set()
seen_destinations = set()
repo_file_cache = {}
repo_resolution_cache = {}
api = HfApi(token=os.environ.get("HF_TOKEN") or None) if HfApi is not None else None


def add_asset_spec(asset_type, destination, source, label):
    if not destination or destination in seen_destinations:
        return
    seen_destinations.add(destination)
    print("\t".join((asset_type, destination, source, label)))


def normalize_hf_url(url):
    normalized = url.strip().strip('"').strip("'")
    if "](https://huggingface.co/" in normalized:
        normalized = "https://huggingface.co/" + normalized.split("](https://huggingface.co/", 1)[1]
    normalized = normalized.split("\\n", 1)[0]
    normalized = normalized.split("\\r", 1)[0]
    return normalized.rstrip("]")


def canonicalize_hf_url(url):
    normalized = normalize_hf_url(url)
    return normalized.replace("/blob/", "/resolve/")


def normalize_relpath(value):
    return value.replace("\\", "/").lstrip("/")


def basename(value):
    return os.path.basename(normalize_relpath(value))


def looks_like_model_ref(value):
    return isinstance(value, str) and value.endswith(MODEL_EXTENSIONS)


def build_destination(root, value):
    return os.path.join("/workspace/ComfyUI/models", root, normalize_relpath(value))


def node_directory_root(node_type, ref):
    if node_type == "LoadWanVideoClipTextEncoder":
        name = basename(ref).lower()
        return "clip_vision" if ("clip" in name or "visual" in name) else "text_encoders"
    return DIRECTORY_ROOTS.get(node_type)


def iter_workflow_nodes(data):
    stack = []
    nodes = data.get("nodes")
    if isinstance(nodes, list):
        stack.append(nodes)

    definitions = data.get("definitions", {})
    if isinstance(definitions, dict):
        for items in definitions.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                nested_nodes = item.get("nodes")
                if isinstance(nested_nodes, list):
                    stack.append(nested_nodes)
                state_nodes = (item.get("state") or {}).get("nodes")
                if isinstance(state_nodes, list):
                    stack.append(state_nodes)

    while stack:
        current_nodes = stack.pop()
        for node in current_nodes:
            if isinstance(node, dict):
                yield node


def collect_hf_urls(text):
    urls = {normalize_hf_url(url) for url in MD_LINK_RE.findall(text)}
    urls.update(normalize_hf_url(url) for url in PLAIN_URL_RE.findall(text))
    return [url for url in urls if url]


def parse_repo_candidate(url):
    parsed = urlparse(url)
    if parsed.netloc != "huggingface.co":
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    repo_id = f"{parts[0]}/{parts[1]}"
    prefix = ""
    if len(parts) >= 5 and parts[2] in {"blob", "resolve", "tree"}:
        if parts[2] == "tree":
            prefix = "/".join(parts[4:]).strip("/")
        else:
            prefix = os.path.dirname("/".join(parts[4:])).strip("/")
    return repo_id, prefix


def direct_file_url(url):
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 5 and parts[2] in {"blob", "resolve"} and parts[-1].endswith(MODEL_EXTENSIONS)


def repo_files(repo_id):
    if repo_id in repo_file_cache:
        return repo_file_cache[repo_id]

    if api is None:
        repo_file_cache[repo_id] = []
        return repo_file_cache[repo_id]

    try:
        repo_file_cache[repo_id] = api.list_repo_files(repo_id=repo_id)
    except Exception as exc:
        print(f"WARNING: failed to list Hugging Face repo files for {repo_id}: {exc}", file=sys.stderr)
        repo_file_cache[repo_id] = []
    return repo_file_cache[repo_id]


def stripped_relparts(ref):
    parts = [part for part in normalize_relpath(ref).split("/") if part]
    while parts and parts[0].lower() in STRIPPED_PATH_PREFIXES:
        parts = parts[1:]
    return parts


def choose_direct_url(ref, direct_urls_by_basename):
    candidates = direct_urls_by_basename.get(basename(ref), [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    ref_suffix = "/".join(stripped_relparts(ref)).lower()
    if basename(ref).lower() in GENERIC_HF_BASENAMES and not ref_suffix:
        return None
    best_url = None
    best_score = -1
    for candidate in candidates:
        score = 1
        lowered = candidate.lower()
        if ref_suffix and ref_suffix in lowered:
            score += len(ref_suffix)
        elif basename(ref).lower() in GENERIC_HF_BASENAMES:
            continue
        if score > best_score:
            best_url = candidate
            best_score = score
    return best_url


def choose_repo_url(ref, repo_candidates):
    cache_key = (tuple(repo_candidates), basename(ref), normalize_relpath(ref))
    if cache_key in repo_resolution_cache:
        return repo_resolution_cache[cache_key]

    target_basename = basename(ref)
    ref_parts = stripped_relparts(ref)
    generic_basename = target_basename.lower() in GENERIC_HF_BASENAMES
    best_match = None

    for repo_id, prefix in repo_candidates:
        files = repo_files(repo_id)
        if not files:
            continue

        for file_path in files:
            if file_path != target_basename and not file_path.endswith("/" + target_basename):
                continue

            score = 1
            lowered = file_path.lower()
            prefix_lower = prefix.lower()
            if prefix_lower and (lowered == prefix_lower or lowered.startswith(prefix_lower + "/")):
                score += 8

            if generic_basename and not prefix_lower and not any(fragment.lower() in lowered for fragment in ref_parts[:-1]):
                continue

            for size in range(len(ref_parts), 0, -1):
                suffix = "/".join(ref_parts[-size:]).lower()
                if lowered.endswith(suffix):
                    score += 20 + size
                    break

            for fragment in ref_parts[:-1]:
                if fragment.lower() in lowered:
                    score += 1

            if best_match is None or score > best_match[0]:
                best_match = (score, repo_id, file_path)

    resolved = None
    if best_match is not None:
        resolved = f"https://huggingface.co/{best_match[1]}/resolve/main/{best_match[2]}"

    repo_resolution_cache[cache_key] = resolved
    return resolved


def property_model_specs(node):
    widgets = node.get("widgets_values")
    if not isinstance(widgets, list):
        return []

    properties = node.get("properties") or {}
    models = [
        model
        for model in properties.get("models", [])
        if isinstance(model, dict) and model.get("name") and model.get("url") and model.get("directory")
    ]
    if not models:
        return []

    specs = []
    selected_refs = [value for value in widgets if looks_like_model_ref(value)]
    if not selected_refs:
        return specs

    for ref in selected_refs:
        official = None
        ref_basename = basename(ref)
        for model in models:
            if model["name"] == ref_basename:
                official = model
                break
        if official is None and len(models) == 1:
            official = models[0]
        if official is None:
            continue

        directory = official["directory"].strip("/")
        official_dest = os.path.join("/workspace/ComfyUI/models", directory, official["name"])
        specs.append((
            "file",
            official_dest,
            canonicalize_hf_url(official["url"]),
            f"Workflow asset {official['name']}",
        ))

        alias_dest = os.path.join("/workspace/ComfyUI/models", directory, normalize_relpath(ref))
        if alias_dest != official_dest:
            specs.append((
                "symlink",
                alias_dest,
                official_dest,
                f"Workflow asset alias {ref_basename}",
            ))

    return specs

for profile in profiles:
    if profile in seen_profiles:
        continue
    seen_profiles.add(profile)
    group_names = profile_map.get(profile, [profile] if profile in groups else None)
    if group_names is None:
        print(f"WARNING: unknown asset profile: {profile}", file=sys.stderr)
        continue
    for group_name in group_names:
        for asset in groups.get(group_name, []):
            destination = asset["dest"]
            asset_type = asset.get("type", "file")
            if asset_type == "hf_snapshot":
                source = asset["repo_id"]
            elif asset_type == "symlink":
                source = asset["target"]
            else:
                source = asset["url"]
            source = url_overrides.get(destination) or source
            add_asset_spec(asset_type, destination, source, asset.get("label", os.path.basename(destination)))

for module in effective_modules:
    module_dir = Path("/workspace/ComfyUI/custom_nodes") / module
    if not module_dir.is_dir():
        continue

    workflow_paths = []
    for dirname in WORKFLOW_DIRS:
        workflow_paths.extend(sorted((module_dir / dirname).glob("*.json")))
    if not workflow_paths:
        continue

    direct_urls_by_basename = {}
    repo_candidates = []
    seen_repo_candidates = set()
    workflow_nodes = []

    for workflow_path in workflow_paths:
        try:
            text = workflow_path.read_text(encoding="utf-8")
            data = json.loads(text)
        except Exception as exc:
            print(f"WARNING: failed to parse workflow {workflow_path}: {exc}", file=sys.stderr)
            continue

        for url in collect_hf_urls(text):
            if direct_file_url(url):
                canonical = canonicalize_hf_url(url)
                direct_urls_by_basename.setdefault(os.path.basename(urlparse(canonical).path), [])
                if canonical not in direct_urls_by_basename[os.path.basename(urlparse(canonical).path)]:
                    direct_urls_by_basename[os.path.basename(urlparse(canonical).path)].append(canonical)
            candidate = parse_repo_candidate(url)
            if candidate is not None and candidate not in seen_repo_candidates:
                repo_candidates.append(candidate)
                seen_repo_candidates.add(candidate)

        workflow_nodes.extend(iter_workflow_nodes(data))

    unresolved = []
    for node in workflow_nodes:
        for asset_type, destination, source, label in property_model_specs(node):
            add_asset_spec(asset_type, destination, source, label)

        widgets = node.get("widgets_values")
        if not isinstance(widgets, list):
            continue

        node_type = node.get("type")
        for ref in widgets:
            if not looks_like_model_ref(ref):
                continue

            root = node_directory_root(node_type, ref)
            if not root:
                continue

            destination = build_destination(root, ref)
            if destination in seen_destinations:
                continue

            source = SOURCE_OVERRIDES.get(normalize_relpath(ref)) or SOURCE_OVERRIDES.get(basename(ref))
            if source is None:
                source = choose_direct_url(ref, direct_urls_by_basename)
            if source is None:
                source = choose_repo_url(ref, repo_candidates)

            if source is None:
                unresolved.append((node_type, ref))
                continue

            add_asset_spec("file", destination, source, f"{module} workflow asset {basename(ref)}")

    if unresolved:
        preview = ", ".join(sorted({ref for _, ref in unresolved})[:8])
        print(
            f"WARNING: unresolved custom workflow assets for {module}: {preview}",
            file=sys.stderr,
        )
PY
}

emit_custom_node_example_workflow_allowlist() {
    python - "$ASSET_MANIFEST_PATH" <<'PY'
import json
import os
import sys

FALSE_VALUES = {"", "0", "false", "no", "off"}

manifest_path = sys.argv[1]
selected_items = [
    item.strip()
    for item in os.environ.get("COMFY_ASSET_PROFILES", "").split(",")
    if item.strip()
]
explicit_allowlist = [
    item.strip()
    for item in os.environ.get("COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ALLOWLIST", "").split(",")
    if item.strip()
]
enabled_value = os.environ.get("COMFY_CUSTOM_NODE_EXAMPLE_WORKFLOWS_ENABLED")

if enabled_value is not None and enabled_value.strip().lower() not in FALSE_VALUES and not explicit_allowlist:
    raise SystemExit(0)

modules = []
seen = set()


def add_modules(items):
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        modules.append(item)


add_modules(explicit_allowlist)

if selected_items and os.path.exists(manifest_path):
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    selection_metadata = manifest.get("selection_metadata", {})
    for selected_item in selected_items:
        metadata = selection_metadata.get(selected_item, {})
        add_modules(metadata.get("custom_node_example_workflows", []))

if modules:
    print(",".join(modules))
PY
}

bootstrap_asset_profiles() {
    if [ ! -f "$ASSET_MANIFEST_PATH" ]; then
        echo "WARNING: asset manifest not found: $ASSET_MANIFEST_PATH" >&2
        return 0
    fi

    mapfile -t asset_specs < <(emit_requested_asset_specs)
    [ "${#asset_specs[@]}" -gt 0 ] || return 0

    for asset_spec in "${asset_specs[@]}"; do
        IFS=$'\t' read -r asset_type destination source label <<EOF
$asset_spec
EOF
        case "$asset_type" in
            hf_snapshot)
                parent_dir="$(dirname "$destination")"
                if ! ensure_optional_writable_dir "$parent_dir"; then
                    echo "WARNING: skipping $label because destination is not writable: $parent_dir" >&2
                    continue
                fi
                download_hf_snapshot_if_missing "$destination" "$source" "$label" || true
                ;;
            symlink)
                parent_dir="$(dirname "$destination")"
                if ! ensure_optional_writable_dir "$parent_dir"; then
                    echo "WARNING: skipping $label because destination is not writable: $parent_dir" >&2
                    continue
                fi
                create_symlink_if_missing "$destination" "$source" "$label" || true
                ;;
            *)
                parent_dir="$(dirname "$destination")"
                if ! ensure_optional_writable_dir "$parent_dir"; then
                    echo "WARNING: skipping $label because destination is not writable: $parent_dir" >&2
                    continue
                fi
                download_if_missing "$destination" "$source" "$label" || true
                ;;
        esac
    done
}

main() {
    command="${1:-bootstrap}"
    case "$command" in
        allowlist)
            emit_custom_node_example_workflow_allowlist
            ;;
        bootstrap)
            bootstrap_asset_profiles
            ;;
        *)
            echo "ERROR: unknown bootstrap helper command: $command" >&2
            return 1
            ;;
    esac
}

main "$@"