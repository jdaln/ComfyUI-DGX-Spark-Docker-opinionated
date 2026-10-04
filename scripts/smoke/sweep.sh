#!/bin/bash
# Run smoke lanes unattended, for an overnight sweep.
#
#   scripts/smoke/sweep.sh                    every lane in lanes.json
#   scripts/smoke/sweep.sh krea-2-turbo ...   only these profiles
#   scripts/smoke/sweep.sh --status           progress of the current sweep
#
# The sweep runs in a detached process on the host, so it carries on after
# this shell closes. Lanes run one at a time inside the container behind
# run_lanes.py's memory gate. Some custom nodes leave memory mapped in the
# ComfyUI process that /free cannot return (HeartMuLa holds about 15 GB until
# a restart), so when the gate stops the run, the sweep restarts the container
# and carries on from the lane it stopped at, at most SWEEP_RESTARTS (3) times.
# At the end it audits the models of the lanes it ran and builds a contact
# sheet of their outputs. Everything lands in tmp/sweep/ in the repository.
# It refuses to start while ComfyUI is busy, so nothing else shares the GPU.
set -u
SMOKE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SMOKE/../.." && pwd)"
OUT="$REPO/tmp/sweep"

if [ "${1:-}" = "--status" ]; then
    tail -n 25 "$OUT/sweep.log"
    ls "$OUT"
    exit 0
fi

comfy_up() {
    docker exec comfyui python3 -c "
import json, urllib.request
q = json.loads(urllib.request.urlopen('http://127.0.0.1:8188/queue', timeout=30).read())
print(len(q['queue_running']) + len(q['queue_pending']))" 2>/dev/null
}

stage() {
    for f in wf_smoke.py run_lanes.py lanes.json audit_refs.py contact_sheet.py; do
        docker cp "$SMOKE/$f" comfyui:/tmp/
    done
}

if [ "${1:-}" != "--supervise" ]; then
    if pgrep -f "sweep.sh --supervise" >/dev/null; then
        echo "a sweep is already running; see: $0 --status" >&2
        exit 1
    fi
    busy=$(comfy_up) || busy=""
    if [ -z "$busy" ]; then
        echo "ComfyUI is not answering on 8188; start it and wait for the server first" >&2
        exit 1
    fi
    if [ "$busy" != "0" ]; then
        echo "ComfyUI has $busy prompt(s) running or queued; let them finish first" >&2
        exit 1
    fi
    rm -rf "$OUT" && mkdir -p "$OUT"
    setsid nohup "$SMOKE/sweep.sh" --supervise "$@" > "$OUT/sweep.log" 2>&1 < /dev/null &
    echo "sweep started; follow it with: $0 --status"
    exit 0
fi

shift
if [ $# -gt 0 ]; then
    remaining=("$@")
else
    mapfile -t remaining < <(python3 -c "
import json, sys
print('\n'.join(l[0] for l in json.load(open(sys.argv[1]))['lanes']))" "$SMOKE/lanes.json")
fi
ran=("${remaining[@]}")

restarts=${SWEEP_RESTARTS:-3}
gate=()
[ -n "${LANE_MIN_AVAILABLE_GIB:-}" ] && gate+=(-e "LANE_MIN_AVAILABLE_GIB=$LANE_MIN_AVAILABLE_GIB")
[ -n "${LANE_GATE_WAIT_S:-}" ] && gate+=(-e "LANE_GATE_WAIT_S=$LANE_GATE_WAIT_S")
for attempt in $(seq 0 "$restarts"); do
    stage
    docker exec comfyui rm -f /tmp/lane_report.json
    docker exec "${gate[@]}" comfyui python3 -u /tmp/run_lanes.py "${remaining[@]}" | tee "$OUT/run.$attempt.log"
    docker cp comfyui:/tmp/lane_report.json "$OUT/lane_report.$attempt.json"
    stopped=$(grep -o 'STOP: .* before [^ ,]*' "$OUT/run.$attempt.log" | sed 's/.* before //')
    [ -n "$stopped" ] || break
    [ "$attempt" -lt "$restarts" ] || break
    # carry on from the lane the gate stopped at, after a restart frees the process
    mapfile -t remaining < <(printf '%s\n' "${remaining[@]}" | awk -v s="$stopped" '$0 == s {on = 1} on')
    echo "--- restarting ComfyUI to free what /free could not, then continuing at $stopped"
    docker restart comfyui
    for _ in $(seq 1 90); do
        [ "$(comfy_up)" = "0" ] && break
        sleep 10
    done
done

python3 - "$OUT" <<'EOF'
import glob, json, sys
merged = {}
for path in sorted(glob.glob(f"{sys.argv[1]}/lane_report.*.json")):
    merged.update(json.load(open(path)))
json.dump(merged, open(f"{sys.argv[1]}/lane_report.json", "w"), indent=1)
passed = sum(1 for v in merged.values() if v["status"] == "PASS")
print(f"\n{passed}/{len(merged)} lanes passed in total")
EOF
docker cp "$OUT/lane_report.json" comfyui:/tmp/sweep_report.json
docker exec comfyui python3 /tmp/audit_refs.py "${ran[@]}" > "$OUT/audit.log" 2>&1
docker exec comfyui python3 /tmp/contact_sheet.py /tmp/sweep_sheet.jpg --report /tmp/sweep_report.json > "$OUT/sheet.log" 2>&1
docker cp comfyui:/tmp/sweep_sheet.jpg "$OUT/sheet.jpg" 2>/dev/null
echo done > "$OUT/finished"
