#!/usr/bin/env python3
"""Audit that every model referenced by each lane's workflow resolves on disk.

A passing smoke lane only proves the graph executed - the harness can paper
over a missing model with a substitution, an inner-node default or a stub.
This audit checks the stronger promise: a user who provisions a profile and
opens its workflow in the browser sees no missing models.
"""
import json, os, re, sys, glob

MODELS_DIR = "/workspace/ComfyUI/models"
TPL_DIR = "/workspace/venv/lib/python3*/site-packages/comfyui_workflow_templates_json/templates"
MODEL_RE = re.compile(r"\.(safetensors|gguf|sft|ckpt|pt|pth|onnx|bin)$", re.I)


def resolve_workflow(target):
    if os.path.exists(target):
        return target
    hits = glob.glob(f"{TPL_DIR}/{target}.json")
    return hits[0] if hits else None


def model_refs(wf):
    """Collect (name, directory_hint) for every model a workflow loads."""
    refs = {}

    def scan(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            if n.get("mode") in (2, 4):      # muted / bypassed
                continue
            # embedded metadata carries the directory the file belongs in
            for meta in ((n.get("properties") or {}).get("models") or []):
                name = meta.get("name")
                if isinstance(name, str) and MODEL_RE.search(name):
                    refs.setdefault(name.replace("\\", "/"), set()).add(meta.get("directory"))
            for w in n.get("widgets_values") or []:
                if isinstance(w, str) and MODEL_RE.search(w) and not w.startswith("http"):
                    refs.setdefault(w.replace("\\", "/"), set())
    scan(wf.get("nodes"))
    for sg in (wf.get("definitions") or {}).get("subgraphs") or []:
        scan(sg.get("nodes"))
    return refs


def exists(name, dirs):
    for d in sorted({d for d in dirs if d} | {""}):
        if os.path.exists(os.path.join(MODELS_DIR, d, name)):
            return os.path.join(d, name)
    # fall back to a search across the model roots
    for root in sorted(os.listdir(MODELS_DIR)):
        cand = os.path.join(MODELS_DIR, root, name)
        if os.path.exists(cand):
            return os.path.join(root, name)
    return None


def main():
    lanes = json.load(open("/tmp/lanes.json"))["lanes"]
    only = set(sys.argv[1:])
    missing_total, checked = {}, 0
    for lane in lanes:
        profile, target = lane[0], lane[1]
        subs = lane[2] if len(lane) > 2 else {}
        if only and profile not in only:
            continue
        path = resolve_workflow(target)
        if path is None:
            print(f"{profile:46s} WORKFLOW NOT FOUND: {target}")
            continue
        checked += 1
        refs = model_refs(json.load(open(path)))
        missing, substituted = [], []
        for name, dirs in sorted(refs.items()):
            if name in subs:                       # lane deliberately swaps this one
                substituted.append(name)
                name = subs[name]
            if exists(os.path.basename(name), dirs) is None and exists(name, dirs) is None:
                missing.append(name)
        status = "OK" if not missing else f"MISSING {len(missing)}"
        print(f"{profile:46s} {status}")
        for m in missing:
            print(f"      - {m}")
        for s in substituted:
            print(f"      ~ lane substitutes {s}")
        if missing:
            missing_total[profile] = missing

    print(f"\n{checked - len(missing_total)}/{checked} lane workflows fully resolve on disk")
    if missing_total:
        names = sorted({m for v in missing_total.values() for m in v})
        print("distinct missing files:")
        for n in names:
            print("  ", n)
    return 1 if missing_total else 0


if __name__ == "__main__":
    sys.exit(main())
