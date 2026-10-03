#!/bin/bash
# Run smoke lanes unattended inside the comfyui container, for an overnight sweep.
#
#   scripts/smoke/sweep.sh                    every lane in lanes.json
#   scripts/smoke/sweep.sh krea-2-turbo ...   only these profiles
#   scripts/smoke/sweep.sh --status           progress of the current sweep
#
# The harness is copied into the container and run_lanes.py starts there,
# detached, so the sweep carries on after this shell closes. Lanes run one at a
# time behind run_lanes.py's memory gate. After the last lane it audits the
# models of the lanes it ran and builds a contact sheet of their outputs.
# Everything lands in /tmp/sweep/ inside the container; collect it with
#   docker cp comfyui:/tmp/sweep ./tmp/
# It refuses to start while ComfyUI is busy, so nothing else shares the GPU.
set -eu
cd "$(dirname "$0")"

if [ "${1:-}" = "--status" ]; then
    docker exec comfyui sh -c 'tail -n 25 /tmp/sweep/sweep.log; ls /tmp/sweep'
    exit 0
fi

if docker exec comfyui pgrep -f /tmp/run_lanes.py >/dev/null; then
    echo "a sweep is already running; see: $0 --status" >&2
    exit 1
fi
busy=$(docker exec comfyui python3 -c "
import json, urllib.request
q = json.loads(urllib.request.urlopen('http://127.0.0.1:8188/queue', timeout=30).read())
print(len(q['queue_running']) + len(q['queue_pending']))") || {
    echo "ComfyUI is not answering on 8188; start it and wait for the server first" >&2
    exit 1
}
if [ "$busy" != "0" ]; then
    echo "ComfyUI has $busy prompt(s) running or queued; let them finish first" >&2
    exit 1
fi

for f in wf_smoke.py run_lanes.py lanes.json audit_refs.py contact_sheet.py; do
    docker cp "$f" comfyui:/tmp/
done
docker exec comfyui sh -c 'rm -rf /tmp/sweep /tmp/lane_report.json && mkdir -p /tmp/sweep'
docker exec -d comfyui sh -c "
python3 -u /tmp/run_lanes.py $* > /tmp/sweep/sweep.log 2>&1
cp /tmp/lane_report.json /tmp/sweep/
python3 /tmp/audit_refs.py $* > /tmp/sweep/audit.log 2>&1
python3 /tmp/contact_sheet.py /tmp/sweep/sheet.jpg --report /tmp/sweep/lane_report.json > /tmp/sweep/sheet.log 2>&1
echo done > /tmp/sweep/finished"
echo "sweep started in the container; follow it with: $0 --status"
