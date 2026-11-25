"""What a Hugging Face model repository holds: gating, licence, revision, and every file with size and sha256.

Usage, in a container (the host may not reach the Hub):
    docker run --rm --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp --user 1000:1000 \
        -e HF_TOKEN -v "$PWD/scripts/tools:/t:ro" --entrypoint python3 comfyui-dgx-spark-docker-opinionated-comfy \
        -I /t/hf_repo.py REPO [REVISION] [FILTER ...]

FILTER words keep only paths containing one of them. The sha256 is the LFS object id, the one a download is checked
against (the ETag of a Xet-backed file is not). Folder totals are printed too, to size a download before starting it.
List the real files before planning around a repository: a release can depend on another one.
"""
import json
import os
import sys
import urllib.error
import urllib.request

repo = sys.argv[1]
rev = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "main"
words = sys.argv[3:]
token = os.environ.get("HF_TOKEN")
headers = {"Authorization": f"Bearer {token}"} if token else {}


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60))


try:
    info = get(f"https://huggingface.co/api/models/{repo}/revision/{rev}")
except urllib.error.HTTPError as exc:
    raise SystemExit(f"{repo}@{rev}: HTTP {exc.code} {exc.reason}")
card = info.get("cardData") or {}
print(f"{repo} revision {info['sha']} gated={info.get('gated')} licence={card.get('license')} "
      f"{card.get('license_name') or ''}".rstrip())
tree = get(f"https://huggingface.co/api/models/{repo}/tree/{info['sha']}?recursive=true")
folders = {}
for entry in tree:
    if entry["type"] != "file" or (words and not any(w in entry["path"] for w in words)):
        continue
    size = entry.get("size") or 0
    folder = entry["path"].rsplit("/", 1)[0] if "/" in entry["path"] else "."
    folders[folder] = folders.get(folder, 0) + size
    print(f"  {entry['path']}  {size / 1e9:.3f} GB  {(entry.get('lfs') or {}).get('oid') or '-'}")
print("folders:", ", ".join(f"{k} {v / 1e9:.2f} GB" for k, v in sorted(folders.items())))
