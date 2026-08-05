#!/usr/bin/env python3
"""Offline checks on the asset manifest, the bundled templates and the smoke lanes.

`run_lanes.py` and `audit_refs.py` both need a running container with the models
already on disk, so the first feedback on a new profile arrives after a multi-GiB
download. This script answers the cheaper question first, from the checkout alone:

  * does every profile resolve to groups that exist, with well-formed entries;
  * does every bundled template load, link up, and use node types this repo ships;
  * is every model a bundled template or a lane workflow loads actually provisioned
    by the manifest (or self-provisioning through embedded `properties.models`).

Run it from the repo root, no container needed:

    python3 scripts/smoke/validate_manifest.py

Exit status is non-zero when anything fails. It is a static check: it cannot tell
you that a graph produces a good image, only that it can be provisioned and opened.
"""
import glob
import json
import os
import re
import sys

MODELS_PREFIX = "/workspace/ComfyUI/models/"
COMFY_PREFIX = "/workspace/ComfyUI/"
# docker-compose mounts ./custom_nodes over the checkout's own directory
CUSTOM_NODES_MOUNT = "/workspace/ComfyUI/custom_nodes/"
MODEL_RE = re.compile(r"\.(safetensors|gguf|sft|ckpt|pt|pth|onnx|bin)$", re.I)

# Frontend-only nodes: they never reach the backend, and wf_smoke.py drops them too.
VIRTUAL_TYPES = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "SetNode", "GetNode"}


def fail(problems, message):
    problems.append(message)


def warn(warnings, message):
    warnings.append(message)


# --------------------------------------------------------------------------- core


def core_node_types(root):
    """Every node id the mounted ComfyUI checkout registers, V1 mappings and V3 schemas."""
    types = set()
    mapping_re = re.compile(r"NODE_CLASS_MAPPINGS\s*(?::[^=]+)?=\s*\{(.*?)\n\}", re.S)
    dq_key_re = re.compile(r'"([^"]+)"\s*:')
    sq_key_re = re.compile(r"'([^']+)'\s*:")
    v3_re = re.compile(r"""node_id\s*=\s*["']([^"']+)["']""")

    sources = [os.path.join(root, "ComfyUI", "nodes.py")]
    sources += sorted(glob.glob(os.path.join(root, "ComfyUI", "comfy_extras", "**", "*.py"), recursive=True))
    for path in sources:
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        for block in mapping_re.findall(text):
            types.update(dq_key_re.findall(block))
            types.update(sq_key_re.findall(block))
        types.update(v3_re.findall(text))
    return types


def custom_node_dirs(root):
    """Directory names the entrypoint will clone out of custom_nodes.txt."""
    names = set()
    path = os.path.join(root, "custom_nodes", "custom_nodes.txt")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            names.add(os.path.basename(line).removesuffix(".git"))
    # Directories carried in this repo rather than cloned.
    for entry in sorted(glob.glob(os.path.join(root, "custom_nodes", "*", ""))):
        names.add(os.path.basename(entry.rstrip("/")))
    return names


# ----------------------------------------------------------------------- manifest


def load_manifest(root, problems):
    path = os.path.join(root, "asset-profiles.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        fail(problems, f"asset-profiles.json does not load: {exc}")
        return None


def check_manifest(manifest, node_dirs, problems, warnings):
    """Structural checks, plus the destination index the other checks are built on."""
    groups = manifest.get("groups", {})
    profiles = manifest.get("profiles", {})

    # dest -> source, so a second group claiming the same path with a different URL is caught
    dest_source = {}
    for group_name, entries in groups.items():
        if not isinstance(entries, list) or not entries:
            fail(problems, f"group '{group_name}' is empty or not a list")
            continue
        for entry in entries:
            dest = entry.get("dest")
            kind = entry.get("type", "file")
            if not dest:
                fail(problems, f"group '{group_name}' has an entry with no dest")
                continue
            if not dest.startswith(COMFY_PREFIX):
                fail(problems, f"group '{group_name}' dest is not a container path: {dest}")
            if kind == "file":
                source = entry.get("url")
                if not source:
                    fail(problems, f"group '{group_name}' file entry has no url: {dest}")
            elif kind == "symlink":
                source = entry.get("target")
                if not source:
                    fail(problems, f"group '{group_name}' symlink entry has no target: {dest}")
            elif kind == "hf_snapshot":
                source = entry.get("repo_id")
                if not source:
                    fail(problems, f"group '{group_name}' hf_snapshot entry has no repo_id: {dest}")
            else:
                fail(problems, f"group '{group_name}' has unknown entry type '{kind}': {dest}")
                continue
            if not entry.get("label"):
                fail(problems, f"group '{group_name}' entry has no label: {dest}")
            previous = dest_source.get(dest)
            if previous is not None and previous != source:
                # bootstrap dedupes by destination, so whichever profile is listed first
                # wins. Fine for a file re-hosted in two repos, a bug for anything else.
                warn(warnings, f"two groups claim {dest} from different sources (first profile listed wins):\n      {previous}\n      {source}")
            dest_source[dest] = source

    for profile, group_names in profiles.items():
        if not isinstance(group_names, list) or not group_names:
            fail(problems, f"profile '{profile}' does not list any groups")
            continue
        for group_name in group_names:
            if group_name not in groups:
                fail(problems, f"profile '{profile}' references unknown group '{group_name}'")

    # A symlink whose target nothing downloads produces a dangling link at startup.
    for group_name, entries in groups.items():
        for entry in entries:
            if entry.get("type") != "symlink":
                continue
            target = entry.get("target")
            if target and target not in dest_source:
                fail(problems, f"group '{group_name}' links {entry['dest']} to an unprovisioned target {target}")

    for profile, metadata in manifest.get("selection_metadata", {}).items():
        if profile not in profiles:
            fail(problems, f"selection_metadata names unknown profile '{profile}'")
        for module in metadata.get("custom_node_example_workflows", []):
            if module not in node_dirs:
                fail(problems, f"selection_metadata for '{profile}' exposes '{module}', which custom_nodes.txt never installs")

    for module, module_profiles in manifest.get("custom_node_example_workflow_profiles", {}).items():
        if module not in node_dirs:
            fail(problems, f"custom_node_example_workflow_profiles names '{module}', which custom_nodes.txt never installs")
        for profile in module_profiles:
            if profile not in profiles:
                fail(problems, f"custom_node_example_workflow_profiles['{module}'] names unknown profile '{profile}'")

    return dest_source


def profile_files(manifest, profile):
    """Every filename a profile puts on disk, as basenames and as models/-relative paths."""
    names = set()
    for group_name in manifest["profiles"].get(profile, []):
        for entry in manifest["groups"].get(group_name, []):
            dest = entry.get("dest", "")
            if not dest.startswith(MODELS_PREFIX):
                continue
            relative = dest[len(MODELS_PREFIX):]
            names.add(relative)
            names.add(os.path.basename(relative))
            # hf_snapshot lands a directory; anything under it resolves at runtime
            if entry.get("type") == "hf_snapshot":
                names.add(relative.rstrip("/") + "/")
    return names


# ---------------------------------------------------------------------- workflows


def iter_graph_nodes(workflow):
    """Top-level nodes first, then every subgraph definition's nodes."""
    yield "", workflow.get("nodes") or []
    for subgraph in (workflow.get("definitions") or {}).get("subgraphs") or []:
        if isinstance(subgraph, dict):
            yield subgraph.get("name") or subgraph.get("id") or "subgraph", subgraph.get("nodes") or []


def model_refs(workflow):
    """(ref, node_type) for every model an active node loads - audit_refs.py semantics."""
    refs = {}
    for _scope, nodes in iter_graph_nodes(workflow):
        for node in nodes:
            if not isinstance(node, dict) or node.get("mode") in (2, 4):
                continue
            for meta in ((node.get("properties") or {}).get("models") or []):
                name = meta.get("name")
                if isinstance(name, str) and MODEL_RE.search(name):
                    refs.setdefault(name.replace("\\", "/"), node.get("type"))
            for widget in node.get("widgets_values") or []:
                if isinstance(widget, str) and MODEL_RE.search(widget) and not widget.startswith("http"):
                    refs.setdefault(widget.replace("\\", "/"), node.get("type"))
    return refs


def self_provisioned(workflow):
    """Names carried by embedded properties.models - the module-scan path downloads these."""
    names = set()
    for _scope, nodes in iter_graph_nodes(workflow):
        for node in nodes:
            for meta in ((node.get("properties") or {}).get("models") or []):
                name, url, directory = meta.get("name"), meta.get("url"), meta.get("directory")
                if name and url and directory:
                    names.add(name.replace("\\", "/"))
                    names.add(os.path.basename(name.replace("\\", "/")))
    return names


# The module scan resolves a widget's filename against any Hugging Face repo mentioned
# anywhere in the file, so a bare repo link in a note is enough to make a template
# self-provisioning even with no properties.models on it. Whether that is wanted or not,
# it should never be a surprise.
HF_URL_RE = re.compile(r"https://huggingface\.co/([^/\s)\]\"]+)/([^/\s)\]\"]+)")
DIRECTORY_ROOTS = {
    "UNETLoader": "diffusion_models", "CLIPLoader": "text_encoders",
    "DualCLIPLoader": "text_encoders", "VAELoader": "vae",
    "LoraLoaderModelOnly": "loras", "CLIPVisionLoader": "clip_vision",
}


def scan_provisioned(path, workflow):
    """Refs the example-workflow scan can resolve on its own, and the repos it would use."""
    text = open(path, encoding="utf-8").read()
    repos = {f"{owner}/{name}" for owner, name in HF_URL_RE.findall(text)
             if name not in ("resolve", "blob", "tree")}
    if not repos:
        return set(), repos
    refs = set()
    for _scope, nodes in iter_graph_nodes(workflow):
        for node in nodes:
            if node.get("mode") in (2, 4) or node.get("type") not in DIRECTORY_ROOTS:
                continue
            for widget in node.get("widgets_values") or []:
                if isinstance(widget, str) and MODEL_RE.search(widget):
                    refs.add(widget.replace("\\", "/"))
    return refs, repos


def check_models_metadata(label, workflow, problems):
    """Every embedded models entry must be complete, or bootstrap silently skips it."""
    for _scope, nodes in iter_graph_nodes(workflow):
        for node in nodes:
            for meta in ((node.get("properties") or {}).get("models") or []):
                missing = [key for key in ("name", "url", "directory") if not meta.get(key)]
                if missing:
                    fail(problems, f"{label}: node {node.get('id')} ({node.get('type')}) has a models entry missing {', '.join(missing)}")
                url = meta.get("url") or ""
                if "/blob/" in url:
                    fail(problems, f"{label}: node {node.get('id')} models url uses /blob/ instead of /resolve/: {url}")


def check_graph_integrity(label, workflow, problems):
    """Link ids referenced by nodes must exist and point at nodes that exist."""
    for scope, nodes in iter_graph_nodes(workflow):
        where = f"{label}{' -> ' + scope if scope else ''}"
        node_ids = set()
        for node in nodes:
            node_id = node.get("id")
            if node_id in node_ids:
                fail(problems, f"{where}: duplicate node id {node_id}")
            node_ids.add(node_id)

        if scope:
            # Subgraph links are dicts and reference the -10/-20 io nodes.
            links = (workflow.get("definitions") or {}).get("subgraphs") or []
            link_rows = []
            for subgraph in links:
                if (subgraph.get("name") or subgraph.get("id")) == scope:
                    link_rows = subgraph.get("links") or []
                    break
            known = {row.get("id") for row in link_rows if isinstance(row, dict)}
            endpoints = node_ids | {-10, -20}
            for row in link_rows:
                if not isinstance(row, dict):
                    continue
                for side in ("origin_id", "target_id"):
                    if row.get(side) not in endpoints:
                        fail(problems, f"{where}: link {row.get('id')} {side}={row.get(side)} is not a node in this subgraph")
        else:
            link_rows = workflow.get("links") or []
            known = set()
            for row in link_rows:
                if not isinstance(row, list) or len(row) < 6:
                    fail(problems, f"{where}: malformed link row {row}")
                    continue
                link_id, origin, _oslot, target, _tslot, _type = row[:6]
                known.add(link_id)
                for side, value in (("origin", origin), ("target", target)):
                    if value not in node_ids:
                        fail(problems, f"{where}: link {link_id} {side}={value} is not a node in this graph")

        for node in nodes:
            for slot in node.get("inputs") or []:
                link_id = slot.get("link")
                if link_id is not None and link_id not in known:
                    fail(problems, f"{where}: node {node.get('id')} input '{slot.get('name')}' uses link {link_id}, which no link row defines")
            for slot in node.get("outputs") or []:
                for link_id in slot.get("links") or []:
                    if link_id not in known:
                        fail(problems, f"{where}: node {node.get('id')} output '{slot.get('name')}' uses link {link_id}, which no link row defines")


def check_node_types(label, workflow, known_types, external, node_dirs, problems):
    subgraph_ids = {sg.get("id") for sg in (workflow.get("definitions") or {}).get("subgraphs") or []}
    for scope, nodes in iter_graph_nodes(workflow):
        where = f"{label}{' -> ' + scope if scope else ''}"
        for node in nodes:
            node_type = node.get("type")
            if not node_type or node_type in known_types or node_type in VIRTUAL_TYPES or node_type in subgraph_ids:
                continue
            pack = external.get(node_type)
            if pack is None:
                fail(problems, f"{where}: node {node.get('id')} uses unknown type '{node_type}' - add it to scripts/smoke/external_node_types.json if a custom node provides it")
            elif pack not in node_dirs:
                fail(problems, f"{where}: node {node.get('id')} type '{node_type}' needs '{pack}', which custom_nodes.txt never installs")


def check_workflow_models(label, workflow, provided, pending, problems, warnings):
    embedded = self_provisioned(workflow)
    for ref, node_type in sorted(model_refs(workflow).items()):
        if ref in embedded or os.path.basename(ref) in embedded:
            continue
        if ref in provided or os.path.basename(ref) in provided:
            continue
        if any(ref.startswith(prefix) for prefix in provided if prefix.endswith("/")):
            continue
        if ref in pending:
            warn(warnings, f"{label}: {node_type} loads '{ref}', declared pending - {pending[ref]}")
            continue
        fail(problems, f"{label}: {node_type} loads '{ref}', which no selected profile provisions and no properties.models entry covers")


# -------------------------------------------------------------------------- lanes


def lane_workflow_path(root, target):
    """Map a lane target to a file in this checkout.

    Returns (path, checkable). `checkable` is False for workflows that only exist
    inside the container - packaged templates from the pip package, and example
    workflows belonging to node packs cloned at startup.
    """
    if target.startswith(CUSTOM_NODES_MOUNT):
        relative = target[len(CUSTOM_NODES_MOUNT):]
        pack = relative.split("/", 1)[0]
        local = os.path.join(root, "custom_nodes", relative)
        if os.path.isdir(os.path.join(root, "custom_nodes", pack)):
            return local, True          # a pack carried in this repo: the file must be here
        return local, False             # cloned from custom_nodes.txt at startup
    if target.startswith(COMFY_PREFIX):
        return os.path.join(root, "ComfyUI", target[len(COMFY_PREFIX):]), True
    if os.path.exists(target):
        return target, True
    return target, False                # bare name: a template from the pip package


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    problems = []
    warnings = []

    manifest = load_manifest(root, problems)
    if manifest is None:
        print("\n".join(problems))
        return 1

    node_dirs = custom_node_dirs(root)
    known_types = core_node_types(root)
    if len(known_types) < 300:
        fail(problems, f"only harvested {len(known_types)} core node types - is the ComfyUI submodule checked out?")

    external_path = os.path.join(root, "scripts", "smoke", "external_node_types.json")
    external = {}
    if os.path.exists(external_path):
        with open(external_path, encoding="utf-8") as handle:
            external = json.load(handle).get("types", {})

    pending_path = os.path.join(root, "scripts", "smoke", "pending_models.json")
    pending = {}
    if os.path.exists(pending_path):
        with open(pending_path, encoding="utf-8") as handle:
            pending = json.load(handle).get("pending", {})

    check_manifest(manifest, node_dirs, problems, warnings)

    # Every model the whole manifest can provision, for templates opened without a profile.
    all_provided = set()
    for profile in manifest.get("profiles", {}):
        all_provided |= profile_files(manifest, profile)

    templates = sorted(glob.glob(os.path.join(root, "custom_nodes", "*", "example_workflows", "*.json")))
    provisions_without_profile = {}
    for path in templates:
        label = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as handle:
                workflow = json.load(handle)
        except ValueError as exc:
            fail(problems, f"{label}: not valid JSON: {exc}")
            continue
        check_graph_integrity(label, workflow, problems)
        check_node_types(label, workflow, known_types, external, node_dirs, problems)
        check_models_metadata(label, workflow, problems)
        check_workflow_models(label, workflow, all_provided, pending, problems, warnings)

        # A template in the default allowlist provisions itself on every startup,
        # profile or not. Report what each one pulls so that is always a decision.
        declared = self_provisioned(workflow)
        scanned, repos = scan_provisioned(path, workflow)
        for name in declared | scanned:
            provisions_without_profile.setdefault(os.path.basename(name), set()).add(os.path.basename(label))
        undeclared = sorted(r for r in scanned
                            if r not in declared and os.path.basename(r) not in declared
                            and r not in pending)
        if undeclared:
            warn(warnings, f"{label}: no properties.models for {', '.join(undeclared)}, but the "
                           f"module scan can still resolve them against {', '.join(sorted(repos))} "
                           f"- they download with no profile selected. Add properties.models to "
                           f"make that explicit, or drop the repo link from the notes.")

    lanes_path = os.path.join(root, "scripts", "smoke", "lanes.json")
    with open(lanes_path, encoding="utf-8") as handle:
        lanes = json.load(handle)["lanes"]

    checked_lanes = skipped_lanes = 0
    for lane in lanes:
        profile, target = lane[0], lane[1]
        subs = lane[2] if len(lane) > 2 else {}
        if profile not in manifest.get("profiles", {}):
            fail(problems, f"lane '{profile}' is not a profile in asset-profiles.json")
            continue
        path, checkable = lane_workflow_path(root, target)
        if not checkable:
            skipped_lanes += 1  # only exists inside the container
            continue
        if not os.path.exists(path):
            fail(problems, f"lane '{profile}' points at {target}, which is not in this checkout")
            continue
        checked_lanes += 1
        with open(path, encoding="utf-8") as handle:
            workflow = json.load(handle)
        provided = profile_files(manifest, profile) | set(subs.values()) | {os.path.basename(v) for v in subs.values()}
        embedded = self_provisioned(workflow)
        for ref, node_type in sorted(model_refs(workflow).items()):
            ref = subs.get(ref, ref)
            if ref in provided or os.path.basename(ref) in provided:
                continue
            if ref in embedded or os.path.basename(ref) in embedded:
                continue
            if any(ref.startswith(prefix) for prefix in provided if prefix.endswith("/")):
                continue
            if ref in pending:
                warn(warnings, f"lane '{profile}': {node_type} loads '{ref}', declared pending - {pending[ref]}")
                continue
            fail(problems, f"lane '{profile}': {node_type} loads '{ref}', which the profile does not provision")

    print(f"{len(manifest.get('profiles', {}))} profiles, {len(manifest.get('groups', {}))} groups")
    print(f"{len(templates)} bundled templates checked")
    if provisions_without_profile:
        # Anything listed here lands on a container that has the templates installed and
        # COMFY_ASSET_PROFILES empty. Worth reading before wondering where the disk went.
        print(f"\n{len(provisions_without_profile)} file(s) the bundled templates provision "
              f"with no profile selected:")
        for name in sorted(provisions_without_profile):
            print(f"  {name}")
    print(f"{checked_lanes} lanes checked against this checkout, {skipped_lanes} skipped (only exist in the container)")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for warning in warnings:
            print(f"  - {warning}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
