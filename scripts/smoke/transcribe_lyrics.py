#!/usr/bin/env python3
"""Transcribe the sung lyrics of output files with HeartMuLa's transcriber.

A song lane can pass on audio that only sounds like singing. If the transcript
gives back the workflow's lyrics, the vocals are real. Needs the
heartmula-transcribe profile on disk. It queues one small prompt per file, so
run it only when nothing else is running, like any other GPU job.

    docker cp scripts/smoke/transcribe_lyrics.py comfyui:/tmp/
    docker exec comfyui python3 /tmp/transcribe_lyrics.py audio/YuE2_00001.flac [more ...]

Paths are relative to ComfyUI/output/. Free the models afterwards (POST /free).
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8188"


def call(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"} if data else {})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def transcribe(name):
    prompt = {
        "1": {"class_type": "HeartMuLaTranscriptionLoader", "inputs": {"base_path": "HeartMuLa"}},
        "2": {"class_type": "LoadAudio", "inputs": {"audio": f"{name} [output]"}},
        "3": {"class_type": "HeartMuLaLyricsTranscriber",
              "inputs": {"transcriptor": ["1", 0], "audio": ["2", 0], "max_new_tokens": 256, "num_beams": 2,
                         "condition_on_prev_tokens": False, "logprob_threshold": -1.0,
                         "no_speech_threshold": 0.4, "temperature": 0.0}},
        "4": {"class_type": "PreviewAny", "inputs": {"source": ["3", 0]}},
    }
    pid = call("/prompt", {"prompt": prompt, "client_id": "transcribe-lyrics"})["prompt_id"]
    while True:
        time.sleep(2)
        hist = call(f"/history/{pid}").get(pid)
        if hist and hist["status"].get("completed"):
            return hist["outputs"]["4"]["text"][0].strip()
        if hist and hist["status"].get("status_str") == "error":
            raise RuntimeError(json.dumps(hist["status"]["messages"])[:800])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    for name in sys.argv[1:]:
        try:
            print(f"--- {name}\n{transcribe(name)}\n")
        except RuntimeError as exc:
            print(f"--- {name}: FAILED {exc}")
