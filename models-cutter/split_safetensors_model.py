#!/usr/bin/env python3
"""Split a monolithic .safetensors model into logical parts.

Default groups are oriented for LTX-style files and are created only when
matching keys are present:
- diffusion_models: model.*
- vae: vae.*
- audio_vae: audio_vae.* and vocoder.*
- text_encoders: text_embedding_projection.*

Output is written next to the source file into ./split_files by default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file


GROUPS = {
    "diffusion_models": ("model.",),
    "vae": ("vae.",),
    "audio_vae": ("audio_vae.", "vocoder."),
    "text_encoders": ("text_embedding_projection.",),
}

# Per-group key transforms so output files are directly loadable by Comfy nodes.
# - "vae" must be saved without the "vae." prefix for Load VAE to detect weights.
KEY_PREFIX_STRIP = {
    "vae": "vae.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split a .safetensors model into parts and save them next to source."
        )
    )
    parser.add_argument("model_path", help="Path to source .safetensors model")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: <model_dir>/split_files)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output files if they already exist",
    )
    return parser.parse_args()


def ensure_valid_model_path(model_path: Path) -> None:
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")
    if not model_path.is_file():
        raise ValueError(f"Not a file: {model_path}")
    if model_path.suffix.lower() != ".safetensors":
        raise ValueError("Input must be a .safetensors file")


def collect_groups(all_keys: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for group_name, prefixes in GROUPS.items():
        subset = [k for k in all_keys if k.startswith(prefixes)]
        if subset:
            grouped[group_name] = subset
    return grouped


def out_name(src_stem: str, group_name: str) -> str:
    suffix_map = {
        "diffusion_models": "model",
        "vae": "video_vae",
        "audio_vae": "audio_vae",
        "text_encoders": "text_projection",
    }
    return f"{src_stem}_{suffix_map[group_name]}.safetensors"


def main() -> int:
    args = parse_args()

    model_path = Path(args.model_path).expanduser().resolve()
    ensure_valid_model_path(model_path)

    out_root = (
        Path(args.out_dir).expanduser().resolve()
        if args.out_dir
        else model_path.parent / "split_files"
    )
    out_root.mkdir(parents=True, exist_ok=True)

    with safe_open(str(model_path), framework="pt", device="cpu") as src:
        keys = list(src.keys())
        metadata = src.metadata() or {}

    grouped = collect_groups(keys)
    if not grouped:
        print("No known prefixes found. Nothing to split.", file=sys.stderr)
        return 2

    print(f"Source: {model_path}")
    print(f"Output: {out_root}")
    print("Groups:")
    for group_name, subset in grouped.items():
        print(f"  - {group_name}: {len(subset)} tensors")

    for group_name, subset in grouped.items():
        group_dir = out_root / group_name
        group_dir.mkdir(parents=True, exist_ok=True)

        dst = group_dir / out_name(model_path.stem, group_name)
        if dst.exists() and not args.force:
            raise FileExistsError(
                f"Output exists: {dst}. Use --force to overwrite."
            )

        tensors = {}
        strip_prefix = KEY_PREFIX_STRIP.get(group_name)
        with safe_open(str(model_path), framework="pt", device="cpu") as src:
            for key in subset:
                out_key = key
                if strip_prefix and key.startswith(strip_prefix):
                    out_key = key[len(strip_prefix) :]
                tensors[out_key] = src.get_tensor(key)

        save_file(tensors, str(dst), metadata=metadata)
        print(f"Wrote: {dst} (keys={len(subset)})")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
