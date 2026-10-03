#!/usr/bin/env python3
"""Put lane outputs side by side in one JPEG, to look at a whole sweep at once.

With --report it takes the first image or video each passing lane of a
run_lanes.py report wrote, labelled with the lane. Files can also be given
directly. Videos show their middle frame; transparency shows over magenta.
Audio is skipped; see audio_check.py.

    docker cp scripts/smoke/contact_sheet.py comfyui:/tmp/
    docker exec comfyui python3 /tmp/contact_sheet.py /tmp/sheet.jpg --report /tmp/lane_report.json
    docker cp comfyui:/tmp/sheet.jpg .
"""
import argparse
import ast
import glob
import json
import os
import re

import av
from PIL import Image, ImageDraw

ROOTS = ["/workspace/ComfyUI/output", "/workspace/ComfyUI/temp"]
IMAGE = (".png", ".jpg", ".jpeg", ".webp")
VIDEO = (".mp4", ".webm", ".mov", ".mkv", ".gif")
TILE = 320


def find(name):
    hits = [p for root in ROOTS for p in glob.glob(f"{root}/**/{name}", recursive=True)]
    return max(hits, key=os.path.getmtime) if hits else None


def lane_outputs(report_path):
    for profile, entry in json.load(open(report_path)).items():
        if entry.get("status") != "PASS":
            continue
        found = re.findall(r"outputs: (\[.*?\])", entry.get("tail", ""))
        names = ast.literal_eval(found[-1]) if found else []
        for name in names:
            if name.lower().endswith(IMAGE + VIDEO) and not name.startswith("comfy.compare"):
                path = find(name)
                if path:
                    yield profile, path
                    break


def tile(path):
    if path.lower().endswith(VIDEO):
        with av.open(path) as container:
            frames = [frame.to_image() for frame in container.decode(container.streams.video[0])]
        image = frames[len(frames) // 2]
    else:
        image = Image.open(path)
    image = image.convert("RGBA")
    backdrop = Image.new("RGBA", image.size, (255, 0, 255, 255))
    backdrop.alpha_composite(image)
    image = backdrop.convert("RGB")
    image.thumbnail((TILE, TILE))
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--report")
    parser.add_argument("--columns", type=int, default=4)
    args = parser.parse_intermixed_args()

    items = list(lane_outputs(args.report)) if args.report else []
    items += [(os.path.basename(p), p) for p in args.files]
    if not items:
        parser.error("nothing to show")

    tiles = [(label, tile(path)) for label, path in items]
    rows = (len(tiles) + args.columns - 1) // args.columns
    sheet = Image.new("RGB", (args.columns * (TILE + 8), rows * (TILE + 24)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(tiles):
        x = (index % args.columns) * (TILE + 8)
        y = (index // args.columns) * (TILE + 24)
        sheet.paste(image, (x, y))
        draw.text((x + 2, y + TILE + 6), label[:48], fill="black")
    sheet.save(args.out, quality=85)
    print(f"{len(tiles)} tiles -> {args.out}")


if __name__ == "__main__":
    main()
