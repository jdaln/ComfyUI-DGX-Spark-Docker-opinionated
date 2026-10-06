#!/usr/bin/env python3
"""What would starting with these profiles download, and does it fit?

Reads asset-profiles.json, skips every file already on disk, and asks Hugging
Face (and the other hosts) for the size of the rest. Reports per profile, lists
gated repos the token cannot read, and compares the total with the free space
on the models filesystem. Exits 1 when the download would leave less than
--min-free-gb free, or when a file answers 401/403. Nothing is downloaded.

The bootstrap has no free-space check of its own, and the models usually live
on the system disk, so run this before adding a large set of profiles:

    docker run --rm --entrypoint python3 --env-file .env \\
      -v "$PWD":/repo:ro -w /repo \\
      -v "$PWD/../ComfyData/models":/workspace/ComfyUI/models:ro \\
      -v "$PWD/../ComfyData/input":/workspace/ComfyUI/input:ro \\
      comfyui-dgx-spark-docker-opinionated-comfy \\
      scripts/smoke/plan_downloads.py ltx-2.5-a2v talking-characters

With no profile names it reads COMFY_ASSET_PROFILES from the environment.
Shared files are counted once, under the first profile that needs them.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("HF_TOKEN", "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _size(url, hops=0):
    """Follow redirects by hand. The token is sent to huggingface.co only. Hugging Face puts
    x-linked-size on the redirect to its CDN, which is where the walk stops; small files and
    files shared between repos come back through relative redirects inside huggingface.co."""
    hf = urllib.parse.urlparse(url).netloc == "huggingface.co"
    req = urllib.request.Request(url, method="HEAD")
    if hf and TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        resp = _OPENER.open(req, timeout=60)
        return int(resp.headers.get("x-linked-size") or resp.headers.get("content-length") or 0)
    except urllib.error.HTTPError as err:
        if err.code not in (301, 302, 303, 307, 308) or hops >= 5:
            raise
        if err.headers.get("x-linked-size"):
            return int(err.headers["x-linked-size"])
        return _size(urllib.parse.urljoin(url, err.headers["location"]), hops + 1)


def _head(url):
    """(bytes or None, error or None)."""
    last = None
    for _ in range(3):
        try:
            return _size(url), None
        except urllib.error.HTTPError as err:
            if err.code in (401, 403, 404):
                return None, f"HTTP {err.code}"
            last = f"HTTP {err.code}"
        except Exception as err:  # flaky network: retry, then report
            last = str(err)[:80]
    return None, last


def _snapshot_size(repo):
    req = urllib.request.Request(f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return sum(e.get("size", 0) for e in json.loads(resp.read()) if e.get("type") == "file"), None
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code}"
    except Exception as err:
        return None, str(err)[:80]


def _partial(dest):
    part = dest + ".part"
    return os.path.getsize(part) if os.path.exists(part) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("profiles", nargs="*")
    ap.add_argument("--manifest", default="asset-profiles.json")
    ap.add_argument("--models", default="/workspace/ComfyUI/models", help="where the dests' filesystem is mounted")
    ap.add_argument("--min-free-gb", type=float, default=40.0)
    args = ap.parse_args()

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    profiles = args.profiles or [p.strip() for p in os.environ.get("COMFY_ASSET_PROFILES", "").split(",") if p.strip()]
    unknown = [p for p in profiles if p not in manifest["profiles"]]
    if unknown or not profiles:
        sys.exit(f"unknown or no profiles: {unknown or '(none given)'}")

    owner, todo = {}, []
    for profile in profiles:
        for group in manifest["profiles"][profile]:
            for entry in manifest["groups"][group]:
                dest, kind = entry["dest"], entry.get("type", "file")
                if dest in owner or kind == "symlink":
                    continue
                owner[dest] = profile
                if kind == "hf_snapshot":
                    if not os.path.exists(os.path.join(dest, ".asset-profile-complete")):
                        todo.append((profile, dest, "snapshot", entry["repo_id"]))
                elif not os.path.exists(dest):
                    todo.append((profile, dest, "file", entry["url"]))

    def measure(item):
        profile, dest, kind, src = item
        size, err = _snapshot_size(src) if kind == "snapshot" else _head(src)
        if size is not None and kind == "file":
            size = max(0, size - _partial(dest))
        return item, size, err

    per_profile = {p: 0 for p in profiles}
    problems = []
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for (profile, dest, kind, src), size, err in pool.map(measure, todo):
            if err:
                problems.append(f"{profile}: {err} for {src}")
                continue
            per_profile[profile] += size or 0
            print(f"  {(size or 0) / 1e9:7.2f} GB  {profile:32s} {os.path.relpath(dest, '/workspace/ComfyUI')}")

    total = sum(per_profile.values())
    free = shutil.disk_usage(args.models).free
    print()
    for profile in profiles:
        print(f"{per_profile[profile] / 1e9:8.2f} GB  {profile}")
    print(f"{total / 1e9:8.2f} GB  to download, {free / 1e9:.1f} GB free, "
          f"{(free - total) / 1e9:.1f} GB left after (margin {args.min_free_gb:.0f} GB)")
    for line in problems:
        print("PROBLEM", line)
    short = (free - total) / 1e9 < args.min_free_gb
    if short:
        print("PROBLEM not enough space for this set; drop profiles or free disk first")
    sys.exit(1 if problems or short else 0)


if __name__ == "__main__":
    main()
