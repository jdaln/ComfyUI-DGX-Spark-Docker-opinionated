#!/usr/bin/env python3
"""Unload ComfyUI's cached models once the queue has been idle for a while.

ComfyUI keeps the last model resident so the next run skips the load. That is
the right default on a discrete GPU. On a DGX Spark the GPU shares the host's
memory, so a 40 GB video model left cached after an afternoon's work starves
everything else on the box until the container restarts - easy to not notice
for days.

Activity is detected from the newest entry in /history, not from catching a
non-empty /queue. A job shorter than the poll interval finishes between polls
and is invisible to queue sampling, so a queue-only check never fires for the
fast workflows that make up most of a session.

    COMFY_IDLE_UNLOAD_MINUTES=60        idle minutes before unloading; 0 disables
    COMFY_IDLE_UNLOAD_POLL_SECONDS=60
"""
import json
import os
import sys
import time
import urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('COMFY_PORT', '8188')}"
FALSE = {"", "0", "off", "false", "no"}


def api(path, payload=None, timeout=30):
    req = urllib.request.Request(
        BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={} if payload is None else {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else None


def log(msg):
    print(f"idle-unload: {msg}", flush=True)


def newest_prompt():
    """Id of the most recently finished prompt, or None."""
    hist = api("/history?max_items=1") or {}
    return next(iter(hist), None)


def queue_busy():
    q = api("/queue") or {}
    return bool(q.get("queue_running") or q.get("queue_pending"))


def main():
    raw = os.environ.get("COMFY_IDLE_UNLOAD_MINUTES", "60").strip().lower()
    if raw in FALSE:
        log("disabled")
        return 0
    try:
        idle_limit = int(float(raw) * 60)
    except ValueError:
        log(f"ignoring unparseable COMFY_IDLE_UNLOAD_MINUTES={raw!r}")
        return 0
    poll = int(os.environ.get("COMFY_IDLE_UNLOAD_POLL_SECONDS", "60"))

    log(f"models will be unloaded after {raw} min with no activity")

    # Asset bootstrap can run for a long time before the server binds.
    while True:
        try:
            api("/system_stats", timeout=5)
            break
        except Exception:
            time.sleep(10)

    last_seen = newest_prompt()
    last_active = time.time()
    dirty = False

    while True:
        time.sleep(poll)
        try:
            if queue_busy():
                last_active, dirty = time.time(), True
                continue
            current = newest_prompt()
            if current != last_seen:            # a job finished between polls
                last_seen, last_active, dirty = current, time.time(), True
                continue
        except Exception as exc:
            log(f"server unreachable ({exc}); not unloading")
            continue

        if not dirty:
            continue
        idle = time.time() - last_active
        if idle < idle_limit:
            continue

        log(f"idle for {int(idle // 60)} min, unloading cached models")
        try:
            api("/free", {"unload_models": True, "free_memory": True}, timeout=120)
            log("done")
            dirty = False
        except Exception as exc:
            log(f"POST /free failed ({exc}); will retry next poll")


if __name__ == "__main__":
    sys.exit(main())
