#!/usr/bin/env python3
"""Workflow smoke: UI-format workflow (template name or path) -> expand subgraphs -> API prompt -> queue -> wait."""
import json, sys, time, urllib.request, uuid, glob, os, itertools

BASE = "http://127.0.0.1:8188"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "image_z_image_turbo"
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 600
TPL_DIR = "/workspace/venv/lib/python3*/site-packages/comfyui_workflow_templates_json/templates"
SKIP_TYPES = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "SetNode", "GetNode"}

SUBS = {
    "Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors": "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors",
    "ltx2-squish.safetensors": "ltx-2-19b-ic-lora-detailer.safetensors",
    "gemma_3_12B_it.safetensors": "gemma_3_12B_it_fp4_mixed.safetensors",
    "ltx-2-19b-dev-fp8.safetensors": "ltx-2-19b-dev.safetensors",
    "ltx-2.3-22b-distilled-fp8.safetensors": "ltx-2.3-22b-dev.safetensors",
}
def sub(v):
    if not isinstance(v, str): return v
    if "\\" in v and v.rsplit(".", 1)[-1] in ("safetensors", "gguf", "pt", "sft", "ckpt"):
        v = v.replace("\\", "/")
    return SUBS.get(v, v)

INPUT_DIR = "/workspace/ComfyUI/input"
FALLBACK_INPUT = {"LoadImage": "example.png", "LoadImageMask": "example.png",
                  "LoadVideo": "bedroom.mp4", "VHS_LoadVideo": "bedroom.mp4"}
# explicit widgets_values order for nodes with dynamic subwidgets
NODE_WIDGET_MAP = {
    "Ideogram4PromptBuilderKJ": ["width", "height", "high_level_description", "background",
                                 "style", "style.photo", "aesthetics", "lighting", "medium",
                                 "style_palette_data", "elements_data", "bg_brightness"],
}

def api(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:3000]); sys.exit(1)

_newlink = itertools.count(10_000_000)

def expand_subgraphs(wf):
    """Inline-expand subgraph nodes. Returns (nodes_by_id, links_list, forced_values, dangling)."""
    sgs = {s["id"]: s for s in (wf.get("definitions") or {}).get("subgraphs") or []}
    nodes = {str(n["id"]): json.loads(json.dumps(n)) for n in wf["nodes"]}
    links = [list(l) for l in wf.get("links", [])]  # [id, src, sslot, dst, dslot, type]
    forced = {}  # (node_id, input_name) -> value
    dangling = []  # (src_node_id, src_slot, type) for unconsumed subgraph outputs

    def expand_one(oid):
        o = nodes.pop(oid)
        sg = sgs[o["type"]]
        pre = f"{oid}_"
        inner_nodes = {str(n["id"]): json.loads(json.dumps(n)) for n in sg["nodes"]}
        for iid, n in inner_nodes.items():
            n["id"] = pre + iid
            nodes[pre + iid] = n
        # categorize inner links
        in_links, out_links = {}, {}
        for l in sg.get("links", []):
            og, tg = l["origin_id"], l["target_id"]
            if og == -10:
                in_links.setdefault(l["origin_slot"], []).append((pre + str(tg), l["target_slot"]))
            elif tg == -20:
                out_links[l["target_slot"]] = (pre + str(og), l["origin_slot"])
            else:
                links.append([next(_newlink), pre + str(og), l["origin_slot"], pre + str(tg), l["target_slot"], l.get("type", "*")])
        # outer widget values map in order to widget-inputs
        wv = list(o.get("widgets_values") or [])
        widget_inputs = [i for i in o.get("inputs", []) if i.get("widget")]
        # widgets_values order is only trustworthy when its length matches the
        # promoted widget count; otherwise fall back to the inner nodes' own
        # widgets_values (which carry the blueprint's intended defaults).
        align = len(wv) == len(widget_inputs)
        if wv and not align:
            print(f"subgraph {sg.get('name', o['type'])[:40]}: widgets_values ({len(wv)}) "
                  f"!= promoted widgets ({len(widget_inputs)}); using inner node defaults")
        wi = 0
        # the outer node's inputs[] can be a reordered subset of the subgraph's
        # promoted inputs, so resolve the inner slot by NAME, not by position
        sg_slot_by_name = {inp.get("name"): k for k, inp in enumerate(sg.get("inputs", []))}
        for idx, inp in enumerate(o.get("inputs", [])):
            targets = in_links.get(sg_slot_by_name.get(inp.get("name"), idx), [])
            ext = [l for l in links if str(l[3]) == oid and l[4] == idx]
            if ext:
                l = ext[0]
                links.remove(l)
                for (tid, tslot) in targets:
                    links.append([next(_newlink), l[1], l[2], tid, tslot, l[5]])
                if inp.get("widget") and wi < len(wv):
                    wi += 1  # linked widget still consumes a slot
                    if inp.get("name") in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                        wi += 1
            elif inp.get("widget") and wi < len(wv):
                v = wv[wi]; wi += 1
                if inp.get("name") in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                    wi += 1
                if align:
                    for (tid, tslot) in targets:
                        tin = nodes[tid].get("inputs", [])
                        if tslot < len(tin):
                            forced[(tid, tin[tslot]["name"])] = v
            elif inp.get("widget"):
                pass
        # rewire outer outputs
        consumed = set()
        for l in links[:]:
            if str(l[1]) == oid:
                src = out_links.get(l[2])
                consumed.add(l[2])
                links.remove(l)
                if src:
                    links.append([next(_newlink), src[0], src[1], l[3], l[4], l[5]])
        for slot, out in enumerate(o.get("outputs", [])):
            if slot not in consumed and slot in out_links:
                src = out_links[slot]
                dangling.append((src[0], src[1], out.get("type", "*")))

    changed = True
    while changed:
        changed = False
        for nid in list(nodes):
            if nodes.get(nid, {}).get("type") in sgs:
                expand_one(nid); changed = True
    return nodes, links, forced, dangling

def ui_to_api(wf, object_info):
    nodes, linklist, forced, dangling = expand_subgraphs(wf)
    links = {l[0]: (str(l[1]), l[2], l[5] if len(l) > 5 else "*") for l in linklist}
    by_dst = {}
    for l in linklist:
        by_dst.setdefault(str(l[3]), {})[l[4]] = l[0]
    # KJNodes virtual Set/Get nodes: SetNode stores its input under a key that a
    # matching GetNode reads back, so resolve GetNode to the SetNode's source
    setnode_link = {}
    for nid, n in nodes.items():
        if n.get("type") == "SetNode":
            wvals = n.get("widgets_values") or []
            key = wvals[0] if wvals else None
            lid = by_dst.get(nid, {}).get(0)
            if key is not None and lid is not None:
                setnode_link[key] = lid
    def resolve(link_id):
        src, slot, ltype = links[link_id]
        for _ in range(100):
            n = nodes.get(src)
            if n is None: return None
            if n["type"] == "GetNode":
                wvals = n.get("widgets_values") or []
                lid = setnode_link.get(wvals[0] if wvals else None)
                if lid is None: return None
                src, slot, _t = links[lid]
                continue
            if n["type"] == "Reroute" or n.get("mode") == 4:
                # pass-through: follow input of matching type (Reroute: slot 0)
                nxt = None
                for islot, inp in enumerate(n.get("inputs", [{"name": ""}])) or [(0, {})]:
                    lid = by_dst.get(src, {}).get(islot)
                    if lid is None: continue
                    s2, sl2, t2 = links[lid]
                    if n["type"] == "Reroute" or t2 == ltype or ltype == "*":
                        nxt = (s2, sl2); break
                if nxt is None: return None
                src, slot = nxt
                continue
            return [src, slot]
        return None
    prompt = {}
    for nid, n in nodes.items():
        t = n["type"]
        if t in SKIP_TYPES or n.get("mode") in (2, 4):
            continue
        info = object_info.get(t)
        if info is None:
            raise RuntimeError(f"unknown node type {t}")
        inputs = {}
        for slot, inp in enumerate(n.get("inputs", [])):
            lid = by_dst.get(nid, {}).get(slot)
            if lid is not None:
                r = resolve(lid)
                if r: inputs[inp["name"]] = r
        wv = n.get("widgets_values")
        if isinstance(wv, dict):
            for k, v in wv.items():
                if k not in inputs: inputs[k] = v
        elif t in NODE_WIDGET_MAP:
            wv = list(wv or [])
            for i, name in enumerate(NODE_WIDGET_MAP[t]):
                if i < len(wv) and name not in inputs:
                    inputs[name] = sub(wv[i])
        else:
            wv = list(wv or [])
            decl = info["input"]
            order = list(decl.get("required", {}).items()) + list(decl.get("optional", {}).items())
            wi = 0
            for name, spec in order:
                typ = spec[0]
                cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if typ == "COMFY_DYNAMICCOMBO_V3":
                    # consumes the key slot plus one slot per nested input of the
                    # selected option; nested inputs are named "<parent>.<child>"
                    key = inputs.get(name)
                    if key is None and wi < len(wv):
                        key = wv[wi]; wi += 1
                        inputs[name] = key
                    sel = next((o for o in (cfg.get("options") or []) if o.get("key") == key), None)
                    if sel:
                        nested = list((sel["inputs"].get("required") or {}).items()) + \
                                 list((sel["inputs"].get("optional") or {}).items())
                        for cname, _cspec in nested:
                            if wi >= len(wv): break
                            inputs[f"{name}.{cname}"] = sub(wv[wi]); wi += 1
                            if cname in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                                wi += 1
                    continue
                is_widget = isinstance(typ, list) or typ in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO") or cfg.get("widget") or "default" in cfg
                if not is_widget: continue
                if name in inputs:
                    # linked widget still consumes a slot in widgets_values
                    if wi < len(wv): wi += 1
                    if name in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                        wi += 1
                    continue
                if wi < len(wv):
                    v = wv[wi]; wi += 1
                    inputs[name] = sub(v)
                    if name in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                        wi += 1
        for (fnid, fname), v in forced.items():
            if fnid == nid:
                inputs[fname] = sub(v)
        # fill missing required widget inputs from declared defaults
        for name, spec in (info["input"].get("required") or {}).items():
            if name in inputs: continue
            typ = spec[0]
            cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if typ == "COMFY_DYNAMICCOMBO_V3":
                opts = cfg.get("options") or []
                if not opts: continue
                sel = opts[0]
                inputs[name] = sel["key"]
                nested = list((sel["inputs"].get("required") or {}).items()) + \
                         list((sel["inputs"].get("optional") or {}).items())
                for cname, cspec in nested:
                    ccfg = cspec[1] if len(cspec) > 1 and isinstance(cspec[1], dict) else {}
                    if "default" in ccfg:
                        inputs[f"{name}.{cname}"] = ccfg["default"]
                    elif isinstance(cspec[0], list) and cspec[0]:
                        inputs[f"{name}.{cname}"] = cspec[0][0]
                print(f"defaulted dynamic combo {t}.{name} = {sel['key']!r}")
            elif "default" in cfg:
                inputs[name] = cfg["default"]
            elif isinstance(typ, list) and typ:
                inputs[name] = typ[0]
        if t in FALLBACK_INPUT:
            for k, v in list(inputs.items()):
                if isinstance(v, str) and "." in v and not os.path.exists(os.path.join(INPUT_DIR, v)):
                    print(f"substituting missing input {v} -> {FALLBACK_INPUT[t]}")
                    inputs[k] = FALLBACK_INPUT[t]
        prompt[nid] = {"class_type": t, "inputs": inputs}
    # prune dangling connection refs (muted/removed sources)
    for nid, node in prompt.items():
        for k in list(node["inputs"]):
            v = node["inputs"][k]
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in prompt:
                del node["inputs"][k]
    # ensure at least one output node; else attach a saver to an orphaned typed output
    has_output = any(object_info.get(p["class_type"], {}).get("output_node") for p in prompt.values())
    if not has_output:
        savers = {"IMAGE": ("SaveImage", "images", {"filename_prefix": "smoke"}),
                  "AUDIO": ("SaveAudioMP3", "audio", {"filename_prefix": "smoke", "quality": "V0"}),
                  "MESH": ("SaveGLB", "mesh", {"filename_prefix": "smoke"}),
                  "VIDEO": ("SaveVideo", "video", {"filename_prefix": "smoke", "format": "mp4", "codec": "h264"})}
        consumed = {(v[0], v[1]) for p in prompt.values() for v in p["inputs"].values()
                    if isinstance(v, list) and len(v) == 2}
        attached = False
        for nid, node in prompt.items():
            outs = object_info.get(node["class_type"], {}).get("output") or []
            for slot, typ in enumerate(outs):
                if typ in savers and (nid, slot) not in consumed:
                    cls, inp, extra = savers[typ]
                    prompt["save_auto"] = {"class_type": cls, "inputs": {inp: [nid, slot], **extra}}
                    print(f"attached {cls} to {node['class_type']}[{slot}]")
                    attached = True
                    break
            if attached: break
    # feed unconnected required IMAGE/MASK/VIDEO inputs from stub loaders
    stubs = {}
    def stub(kind):
        if kind not in stubs:
            if kind == "VIDEO":
                prompt["autoload_video"] = {"class_type": "LoadVideo", "inputs": {"file": "bedroom.mp4"}}
                stubs[kind] = ["autoload_video", 0]
            else:
                prompt["autoload_img"] = {"class_type": "LoadImage", "inputs": {"image": "example.png"}}
                stubs["IMAGE"] = ["autoload_img", 0]
                stubs["MASK"] = ["autoload_img", 1]
        return stubs[kind]
    for nid, node in list(prompt.items()):
        info = object_info.get(node["class_type"]) or {}
        for name, spec in (info.get("input", {}).get("required") or {}).items():
            typ = spec[0]
            if name in node["inputs"] or not isinstance(typ, str):
                continue
            # COMFY_MATCHTYPE_V3 is a wildcard slot; an image satisfies it
            kind = typ if typ in ("IMAGE", "MASK", "VIDEO") else ("IMAGE" if typ == "COMFY_MATCHTYPE_V3" else None)
            if kind is None:
                continue
            node["inputs"][name] = stub(kind)
            print(f"fed unconnected {typ} input {node['class_type']}.{name} from stub loader")
    # fill missing required scalar/combo inputs with object_info defaults
    for nid, node in prompt.items():
        info = object_info.get(node["class_type"]) or {}
        for name, spec in (info.get("input", {}).get("required") or {}).items():
            if name in node["inputs"]:
                continue
            typ = spec[0]
            cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if isinstance(typ, list):
                val = cfg.get("default", typ[0] if typ else "")
            elif typ == "COMBO":
                val = cfg.get("default", (cfg.get("options") or [""])[0])
            elif typ in ("INT", "FLOAT", "BOOLEAN", "STRING"):
                val = cfg.get("default", {"INT": 0, "FLOAT": 0.0, "BOOLEAN": False, "STRING": ""}[typ])
            else:
                continue
            node["inputs"][name] = val
            print(f"defaulted {node['class_type']}.{name} = {str(val)[:40]!r}")
    return prompt

if os.path.exists(TARGET):
    path = TARGET
else:
    cands = glob.glob(f"{TPL_DIR}/{TARGET}.json")
    if not cands:
        names = sorted(os.path.basename(p)[:-5] for p in glob.glob(f"{TPL_DIR}/*.json"))
        print("not found; matches:", [x for x in names if TARGET.lower() in x.lower()][:20]); sys.exit(2)
    path = cands[0]
wf = json.load(open(path))
prompt = ui_to_api(wf, api("/object_info")) if "nodes" in wf else wf

cid = f"smoke-{uuid.uuid4().hex[:8]}"
resp = api("/prompt", {"prompt": prompt, "client_id": cid})
if "prompt_id" not in resp:
    print("QUEUE REJECTED:", json.dumps(resp)[:2000]); sys.exit(1)
pid = resp["prompt_id"]
print("queued", os.path.basename(path), "prompt_id", pid)

t0 = time.time()
while time.time() - t0 < TIMEOUT:
    time.sleep(5)
    hist = api(f"/history/{pid}")
    if pid in hist:
        st = hist[pid].get("status", {})
        if st.get("completed"):
            outs = [o.get("filename") for node in hist[pid].get("outputs", {}).values()
                    for v in node.values() if isinstance(v, list)
                    for o in v if isinstance(o, dict) and o.get("filename")]
            print(f"COMPLETED in {time.time()-t0:.0f}s, outputs:", outs); sys.exit(0)
        if st.get("status_str") == "error":
            msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
            print("FAILED:", json.dumps(msgs, indent=1)[:2000]); sys.exit(1)
print("TIMEOUT after", TIMEOUT, "s"); sys.exit(1)
