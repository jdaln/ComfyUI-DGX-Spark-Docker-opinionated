#!/usr/bin/env python3
"""Workflow smoke: UI-format workflow (template name or path) -> expand subgraphs -> API prompt -> queue -> wait."""
import json, sys, time, urllib.request, uuid, glob, os, itertools, re

BASE = "http://127.0.0.1:8188"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "image_z_image_turbo"
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 600
TPL_DIR = "/workspace/venv/lib/python3*/site-packages/comfyui_workflow_templates_json/templates"
SKIP_TYPES = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "SetNode", "GetNode"}
# the 3D viewer's widgets (Save3DAdvanced's viewport_state): the workflow keeps a
# slot for them in widgets_values, and the frontend sends the viewer's state, a dict
VIEWER_TYPES = {"LOAD_3D", "LOAD_3D_ANIMATION"}

# Global substitutions should stay EMPTY: a workflow that only runs because the
# harness swapped a model is a workflow that is broken for users. Provision the
# real file or add a manifest symlink alias instead. Per-lane subs (lanes.json)
# are still fine - those express "this profile is about a different LoRA".
SUBS = {}
# optional per-lane substitutions (argv[3], JSON object) let two lanes share a
# template while pointing at different checkpoints
if len(sys.argv) > 3:
    SUBS.update(json.loads(sys.argv[3]))
MODEL_FILE = re.compile(r"\.(safetensors|gguf|sft|ckpt|pt|pth|onnx|bin)$", re.I)
_normalized = set()
def sub(v):
    if not isinstance(v, str): return v
    if "\\" in v and MODEL_FILE.search(v):
        # a workflow saved on Windows stores model subfolders as onnx\yolov10m.onnx;
        # the Linux server lists onnx/yolov10m.onnx and the GUI shows the value red
        if v not in _normalized:
            _normalized.add(v)
            print(f"normalized Windows path {v} -> {v.replace(chr(92), '/')}")
        v = v.replace("\\", "/")
    return SUBS.get(v, v)

INPUT_DIR = "/workspace/ComfyUI/input"
FALLBACK_INPUT = {"LoadImage": "example.png", "LoadImageMask": "example.png",
                  "LoadVideo": "minimalist_sky_view_lounge.mp4", "VHS_LoadVideo": "minimalist_sky_view_lounge.mp4"}
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

def accepts(node, slot, value, object_info):
    """Whether `value` could be what input `slot` of `node` holds."""
    info = object_info.get(node["type"])
    if info is None or slot >= len(node.get("inputs", [])):
        return True  # nested subgraph or frontend-only node: nothing to check against
    name = node["inputs"][slot]["name"]
    spec = (info["input"].get("required") or {}).get(name) or (info["input"].get("optional") or {}).get(name)
    if not spec:
        return True
    typ = spec[0]
    cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(typ, list) or typ == "COMBO":
        options = typ if isinstance(typ, list) else cfg.get("options") or []
        if isinstance(value, str) and MODEL_FILE.search(value) and \
                (not options or any(isinstance(o, str) and MODEL_FILE.search(o) for o in options)):
            return True  # a model list: the file may just not be on this machine
        return value in options
    if typ == "BOOLEAN": return isinstance(value, bool)
    if typ == "INT": return isinstance(value, int) and not isinstance(value, bool)
    if typ == "FLOAT": return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "STRING": return isinstance(value, str)
    return True

def promoted_values(sg, wv, in_links, nodes, object_info):
    """Map a subgraph node's widgets_values onto the subgraph's own inputs.

    Newer frontends save promoted widget values in the order of the subgraph's
    inputs while listing only some of them in the node's `inputs`. Use that order
    only when the count works out and every value suits the inner input it lands
    on; anything else returns None and keeps the inner node defaults."""
    values, wi = {}, 0
    for k, sin in enumerate(sg.get("inputs", [])):
        if sin.get("type") not in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COMFY_DYNAMICCOMBO_V3"):
            continue
        if wi >= len(wv): return None
        values[k] = wv[wi]; wi += 1
        if sin.get("type") == "INT" and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
            wi += 1
    if wi != len(wv): return None
    for k, v in values.items():
        for tid, tslot in in_links.get(k, []):
            if not accepts(nodes[tid], tslot, v, object_info): return None
    return values

def proxied_values(o, sg, pre, wv, in_links, nodes, object_info):
    """Map a subgraph node's widgets_values through properties.proxyWidgets.

    Newer frontends list every widget shown on the subgraph node there, in the
    order of widgets_values: ["-1", name] is a subgraph input, [inner_id, name]
    a widget of an inner node shown directly (a sampler's seed). A null value
    stays on the inner widget. Returns ({input slot: value}, {(inner node id,
    input name): value}), or None when the list does not fit."""
    proxy = (o.get("properties") or {}).get("proxyWidgets")
    if not isinstance(proxy, list) or len(proxy) != len(wv):
        return None
    slot_by_name = {inp.get("name"): k for k, inp in enumerate(sg.get("inputs", []))}
    values, inner = {}, {}
    for entry, v in zip(proxy, wv):
        if not (isinstance(entry, list) and len(entry) == 2):
            return None
        owner, name = str(entry[0]), entry[1]
        if v is None or name == "control_after_generate":
            continue
        if owner == "-1":
            k = slot_by_name.get(name)
            if k is None:
                return None
            for tid, tslot in in_links.get(k, []):
                if not accepts(nodes[tid], tslot, v, object_info):
                    return None
            values[k] = v
        else:
            if pre + owner not in nodes:
                return None
            inner[(pre + owner, name)] = v
    return values, inner

def expand_subgraphs(wf, object_info):
    """Inline-expand subgraph nodes.
    Returns (nodes_by_id, links_list, forced_values, dangling, shared)."""
    sgs = {s["id"]: s for s in (wf.get("definitions") or {}).get("subgraphs") or []}
    nodes = {str(n["id"]): json.loads(json.dumps(n)) for n in wf["nodes"]}
    links = [list(l) for l in wf.get("links", [])]  # [id, src, sslot, dst, dslot, type]
    forced = {}  # (node_id, input_name) -> value
    dangling = []  # (src_node_id, src_slot, type) for unconsumed subgraph outputs
    shared = []  # [(node_id, input_name), ...] fed by one subgraph input nothing outside links

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
        # promoted widget count, or when it lines up with the subgraph's own
        # inputs (promoted_values); otherwise fall back to the inner nodes' own
        # widgets_values (which carry the blueprint's intended defaults).
        align = len(wv) == len(widget_inputs)
        promoted = None
        if wv and not align:
            proxied = proxied_values(o, sg, pre, wv, in_links, nodes, object_info)
            if proxied is not None:
                promoted, inner_values = proxied
                forced.update(inner_values)
            else:
                promoted = promoted_values(sg, wv, in_links, nodes, object_info)
        if wv and not align and promoted is None:
            print(f"subgraph {sg.get('name', o['type'])[:40]}: widgets_values ({len(wv)}) "
                  f"!= promoted widgets ({len(widget_inputs)}); using inner node defaults")
        linked = set()
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
                linked.add(inp.get("name"))
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
        for k, v in (promoted or {}).items():
            if sg["inputs"][k].get("name") in linked:
                continue
            for (tid, tslot) in in_links.get(k, []):
                tin = nodes[tid].get("inputs", [])
                if tslot < len(tin):
                    forced[(tid, tin[tslot]["name"])] = v
        # One subgraph input feeding several inner inputs carries one value. When the
        # outer node stores no widget values (newer frontends keep promoted values on
        # the inner widget and list it in properties.proxyWidgets), only the inner
        # widget has it, so the other inputs of the group get it after conversion.
        for k, targets in in_links.items():
            if len(targets) < 2 or k >= len(sg.get("inputs", [])) or sg["inputs"][k].get("name") in linked:
                continue
            shared.append([(tid, nodes[tid]["inputs"][tslot]["name"]) for tid, tslot in targets
                           if tslot < len(nodes[tid].get("inputs", []))])
        # rewire outer outputs
        consumed = set()
        for l in links[:]:
            if str(l[1]) == oid:
                src = out_links.get(l[2])
                consumed.add(l[2])
                links.remove(l)
                if src:
                    links.append([next(_newlink), src[0], src[1], l[3], l[4], l[5]])
                else:
                    # the user's graph would get nothing here either; a stub must not hide it
                    raise RuntimeError(f"subgraph {sg.get('name', o['type'])[:40]}: output {l[2]} is linked "
                                       f"outside but nothing inside feeds it (broken template)")
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
    return nodes, links, forced, dangling, shared

def dyn_incomplete(name, spec, inputs):
    """True when a dynamic combo's selected option lacks a required nested input (recursively)."""
    if not spec or spec[0] != "COMFY_DYNAMICCOMBO_V3" or name not in inputs:
        return False
    cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    sel = next((o for o in (cfg.get("options") or []) if o.get("key") == inputs[name]), None)
    if sel is None:
        return True
    for cname, cspec in ((sel.get("inputs") or {}).get("required") or {}).items():
        full = f"{name}.{cname}"
        ctyp = cspec[0] if cspec else None
        ccfg = cspec[1] if len(cspec) > 1 and isinstance(cspec[1], dict) else {}
        widget = (isinstance(ctyp, list) or ctyp in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COMFY_DYNAMICCOMBO_V3")
                  or ccfg.get("widget") or "default" in ccfg) and not ccfg.get("forceInput")
        if not widget:
            continue  # a socket: no stored value, fed by a link
        if full not in inputs or dyn_incomplete(full, cspec, inputs):
            return True
    return False


def ui_to_api(wf, object_info):
    nodes, linklist, forced, dangling, shared = expand_subgraphs(wf, object_info)
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
            if n["type"] == "SetNode":
                # a SetNode's own output passes its input through, like a reroute
                lid = by_dst.get(src, {}).get(0)
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
                if r and nodes[r[0]]["type"] == "PrimitiveNode":
                    # frontend-only: the frontend sends the primitive's value, not a link
                    inputs[inp["name"]] = sub((nodes[r[0]].get("widgets_values") or [None])[0])
                elif r: inputs[inp["name"]] = r
        wv = n.get("widgets_values")
        if isinstance(wv, dict):
            for k, v in wv.items():
                if k not in inputs: inputs[k] = sub(v)
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
                        for cname, cspec in nested:
                            ctyp = cspec[0]
                            ccfg = cspec[1] if len(cspec) > 1 and isinstance(cspec[1], dict) else {}
                            if not (isinstance(ctyp, list) or ctyp in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO",
                                                                        "COMFY_DYNAMICCOMBO_V3")
                                    or ccfg.get("widget") or "default" in ccfg) or ccfg.get("forceInput"):
                                continue  # a socket (InfiniteTalk's mode.mask_1): no stored value, link kept
                            if wi >= len(wv): break
                            if f"{name}.{cname}" not in inputs:
                                inputs[f"{name}.{cname}"] = sub(wv[wi])
                            wi += 1
                            if cname in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                                wi += 1
                    continue
                is_widget = isinstance(typ, list) or typ in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO") or typ in VIEWER_TYPES or cfg.get("widget") or "default" in cfg
                # forceInput: a socket only, the workflow keeps no widget value for it
                # (SAM2's coordinates_positive took individual_objects' False)
                if not is_widget or cfg.get("forceInput"): continue
                if name in inputs:
                    # linked widget still consumes a slot in widgets_values
                    if wi < len(wv): wi += 1
                    if name in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                        wi += 1
                    continue
                if wi < len(wv):
                    v = wv[wi]; wi += 1
                    inputs[name] = (v if isinstance(v, dict) else {}) if isinstance(typ, str) and typ in VIEWER_TYPES else sub(v)
                    if name in ("seed", "noise_seed") and wi < len(wv) and wv[wi] in ("fixed", "increment", "decrement", "randomize"):
                        wi += 1
        # The positional mapping above follows one level of dynamic combo. A nested
        # one (BuildPoseFile: format -> mesh_style) leaves required inputs unset and
        # shifts every later value. Newer frontends also save widgets_values_named,
        # keyed by API input name; use it for such a node only, since templates
        # edited by script can carry stale named values.
        named = n.get("widgets_values_named")
        decl = {**(info["input"].get("required") or {}), **(info["input"].get("optional") or {})}
        if isinstance(named, dict) and named and any(
                dyn_incomplete(k, s, inputs) for k, s in (info["input"].get("required") or {}).items()):
            print(f"positional values do not fit {t} #{nid}; using widgets_values_named")
            dyn = {k for k, s in decl.items() if s and s[0] == "COMFY_DYNAMICCOMBO_V3"}
            linked = {k for k, v in inputs.items() if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)}
            for k in [k for k in inputs if "." in k and k.split(".")[0] in dyn and k not in named]:
                del inputs[k]
            for k, v in named.items():
                top = k.split(".")[0]
                if top not in decl or k in linked or ("." in k and top not in dyn):
                    continue
                if isinstance(decl[top][0], str) and decl[top][0] in VIEWER_TYPES:
                    v = v if isinstance(v, dict) else {}
                inputs[k] = sub(v)
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
            elif cfg.get("socketless") or (isinstance(typ, str) and typ in VIEWER_TYPES):
                # display-only widget the workflow stores no value for, such as
                # ImageCompare's compare_view; the frontend still sends one
                inputs[name] = {}
        if t in FALLBACK_INPUT:
            for k, v in list(inputs.items()):
                if isinstance(v, str) and "." in v and not os.path.exists(os.path.join(INPUT_DIR, v)):
                    print(f"substituting missing input {v} -> {FALLBACK_INPUT[t]}")
                    inputs[k] = FALLBACK_INPUT[t]
        prompt[nid] = {"class_type": t, "inputs": inputs}
    # a shared subgraph input's value, copied to the inner inputs that got none
    # (SCAIL-2's frame math reads the window length its sampler widget holds)
    for group in shared:
        held = [prompt[t]["inputs"][n] for t, n in group if t in prompt and n in prompt[t]["inputs"]]
        values = [v for v in held if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str))]
        if not values:
            continue
        for t, n in group:
            if t in prompt and n not in prompt[t]["inputs"]:
                prompt[t]["inputs"][n] = values[0]
                print(f"shared subgraph input: {prompt[t]['class_type']}.{n} = {str(values[0])[:40]!r}")
    # A list in a prompt means a link. List-valued widgets (CreateBoundingBoxes'
    # editor_state) go wrapped as {"__value__": [...]}, as the frontend sends them.
    for node in prompt.values():
        for k, v in list(node["inputs"].items()):
            if isinstance(v, list) and not (len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)):
                node["inputs"][k] = {"__value__": v}
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
    # ComfyUI runs only what an output node reads. Drop the rest (a leftover node
    # with nothing connected) so it gets no stub input and no stub warning.
    reached = set()
    todo = [nid for nid, p in prompt.items() if object_info.get(p["class_type"], {}).get("output_node")]
    while todo:
        nid = todo.pop()
        if nid in reached or nid not in prompt: continue
        reached.add(nid)
        todo += [v[0] for v in prompt[nid]["inputs"].values()
                 if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)]
    unread = [nid for nid in prompt if nid not in reached]
    for nid in unread:
        del prompt[nid]
    if unread:
        print(f"left out {len(unread)} node(s) no output reads")
    # feed unconnected required IMAGE/MASK/VIDEO inputs from stub loaders
    stubs = {}
    def stub(kind):
        if kind not in stubs:
            if kind == "VIDEO":
                prompt["autoload_video"] = {"class_type": "LoadVideo", "inputs": {"file": "minimalist_sky_view_lounge.mp4"}}
                stubs[kind] = ["autoload_video", 0]
            else:
                prompt["autoload_img"] = {"class_type": "LoadImage", "inputs": {"image": "example.png"}}
                stubs["IMAGE"] = ["autoload_img", 0]
                stubs["MASK"] = ["autoload_img", 1]
        return stubs[kind]
    def match_template(spec):
        if spec[0] != "COMFY_MATCHTYPE_V3":
            return None
        cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        return (cfg.get("template") or {}).get("template_id")
    for nid, node in list(prompt.items()):
        info = object_info.get(node["class_type"]) or {}
        declared = {**(info.get("input", {}).get("required") or {}), **(info.get("input", {}).get("optional") or {})}
        for name, spec in (info.get("input", {}).get("required") or {}).items():
            typ = spec[0]
            if name in node["inputs"] or not isinstance(typ, str):
                continue
            if typ == "COMFY_MATCHTYPE_V3":
                # wildcard slot: mirror a connected input of the same template so
                # the type is right by construction (e.g. a switch's other branch);
                # only fall back to an image when nothing else is connected
                sib = next((v for k2, v in node["inputs"].items()
                            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)
                            and k2 in declared and declared[k2][0] == typ
                            and match_template(declared[k2]) == match_template(spec)), None)
                if sib is not None:
                    node["inputs"][name] = sib
                    print(f"mirrored sibling into wildcard {node['class_type']}.{name}")
                    continue
                kind = "IMAGE"
            else:
                kind = typ if typ in ("IMAGE", "MASK", "VIDEO") else None
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
if resp.get("node_errors"):
    # ComfyUI drops an output whose node fails validation and runs the rest
    print("NODE ERRORS (outputs dropped):", json.dumps(resp["node_errors"])[:2000], flush=True)
    for path_, body in (("/queue", {"delete": [resp["prompt_id"]]}), ("/interrupt", {"prompt_id": resp["prompt_id"]})):
        try:
            api(path_, body)
        except (Exception, SystemExit):
            pass
    sys.exit(1)
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
