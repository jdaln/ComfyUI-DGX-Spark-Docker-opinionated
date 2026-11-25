"""Download Hugging Face files at a pinned revision, each checked against its sha256 before it is kept.

Usage, in a container with only the destination folder writable:
    docker run --rm --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp --user 1000:1000 \
        -e HF_TOKEN -v "$PWD/scripts/tools:/t:ro" -v DEST_DIR:/o --entrypoint python3 \
        comfyui-dgx-spark-docker-opinionated-comfy -I /t/hf_fetch.py REPO REVISION PATH:/o/NAME:SHA256 ...

SHA256 "-" skips the check (small text files without an LFS id: configs, READMEs). Files already present are left
alone. A download goes to NAME.part first and is renamed only after the check passes. Get revisions and sha256
values from hf_repo.py.
"""
import hashlib
import os
import sys
import urllib.request

repo, rev = sys.argv[1:3]
token = os.environ.get("HF_TOKEN")
for spec in sys.argv[3:]:
    path, dest, sha = spec.rsplit(":", 2)
    if os.path.exists(dest):
        print("exists", dest)
        continue
    digest = hashlib.sha256()
    request = urllib.request.Request(f"https://huggingface.co/{repo}/resolve/{rev}/{path}",
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(request, timeout=120) as response, open(dest + ".part", "wb") as out:
        for block in iter(lambda: response.read(1 << 24), b""):
            digest.update(block)
            out.write(block)
    if sha != "-" and digest.hexdigest() != sha:
        os.remove(dest + ".part")
        raise SystemExit(f"sha256 mismatch for {path}: got {digest.hexdigest()}")
    os.replace(dest + ".part", dest)
    print("ok", dest)
