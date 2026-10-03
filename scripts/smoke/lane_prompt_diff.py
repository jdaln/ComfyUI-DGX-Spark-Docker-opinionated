#!/usr/bin/env python3
"""Show what a change to wf_smoke.py does to the prompt of every lane.

Converts each lane's workflow with two versions of the harness and prints every
input that differs. Nothing is queued and no model loads, so it is safe to run
next to anything; it only needs the server up for /object_info.

    git show HEAD:scripts/smoke/wf_smoke.py > /tmp/wf_smoke_old.py
    docker cp /tmp/wf_smoke_old.py comfyui:/tmp/
    docker cp scripts/smoke/wf_smoke.py comfyui:/tmp/
    docker cp scripts/smoke/lanes.json comfyui:/tmp/
    docker cp scripts/smoke/lane_prompt_diff.py comfyui:/tmp/
    docker exec comfyui python3 /tmp/lane_prompt_diff.py /tmp/wf_smoke_old.py /tmp/wf_smoke.py [profile ...]
"""
import contextlib
import glob
import io
import json
import os
import sys
import urllib.request

TPL_DIR = "/workspace/venv/lib/python3*/site-packages/comfyui_workflow_templates_json/templates"


def load_harness(path, subs):
    """wf_smoke.py runs its lane at import time, so take only the definitions above that."""
    head = open(path).read().split("\nif os.path.exists(TARGET):")[0]
    namespace = {"__name__": "wf_smoke_dry"}
    argv = sys.argv
    sys.argv = ["wf_smoke.py", "none", "1"] + ([json.dumps(subs)] if subs else [])
    try:
        exec(compile(head, path, "exec"), namespace)
    finally:
        sys.argv = argv
    return namespace


def convert(harness_path, workflow, subs, object_info):
    harness = load_harness(harness_path, subs)
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        prompt = harness["ui_to_api"](json.loads(json.dumps(workflow)), object_info)
    return prompt, log.getvalue()


def short(value):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 70 else text[:67] + "..."


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    old_path, new_path, only = sys.argv[1], sys.argv[2], set(sys.argv[3:])
    object_info = json.loads(urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=120).read())
    changed, failed = [], []
    for lane in json.load(open("/tmp/lanes.json"))["lanes"]:
        profile, target = lane[0], lane[1]
        subs = lane[2] if len(lane) > 2 else {}
        if only and profile not in only:
            continue
        path = target if os.path.exists(target) else (glob.glob(f"{TPL_DIR}/{target}.json") or [None])[0]
        if path is None:
            print(f"{profile}: workflow not found ({target})")
            failed.append(profile)
            continue
        workflow = json.load(open(path))
        try:
            old, old_log = convert(old_path, workflow, subs, object_info)
            new, new_log = convert(new_path, workflow, subs, object_info)
        except Exception as exc:
            print(f"{profile}: conversion failed: {exc!r}")
            failed.append(profile)
            continue
        lines = []
        for nid in sorted(set(old) | set(new)):
            a, b = old.get(nid), new.get(nid)
            if a is None or b is None:
                lines.append(f"node {nid} {'added' if a is None else 'removed'} ({(a or b)['class_type']})")
                continue
            for name in sorted(set(a["inputs"]) | set(b["inputs"])):
                va, vb = a["inputs"].get(name, "<unset>"), b["inputs"].get(name, "<unset>")
                if va != vb:
                    lines.append(f"{b['class_type']}#{nid}.{name}: {short(va)} -> {short(vb)}")
        if lines:
            changed.append(profile)
        print(f"=== {profile}: {'CHANGED' if lines else 'same'}")
        for line in lines:
            print("     ", line)
        for line in sorted(set(old_log.splitlines()) ^ set(new_log.splitlines())):
            print("      log:", line[:160])
    print("\nchanged:", ", ".join(changed) or "none")
    print("failed:", ", ".join(failed) or "none")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
