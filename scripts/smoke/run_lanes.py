#!/usr/bin/env python3
"""Run all smoke lanes sequentially, write /tmp/lane_report.json as it goes."""
import json, subprocess, sys, time

lanes = json.load(open('/tmp/lanes.json'))['lanes']
only = sys.argv[1:] if len(sys.argv) > 1 else None
report = {}
for lane in lanes:
    prof, wf = lane[0], lane[1]
    lane_subs = lane[2] if len(lane) > 2 else None
    if only and prof not in only:
        continue
    print(f'=== {prof} :: {wf}', flush=True)
    t0 = time.time()
    cmd = ['python3', '/tmp/wf_smoke.py', wf, '1800']
    if lane_subs:
        cmd.append(json.dumps(lane_subs))
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    status = 'PASS' if r.returncode == 0 else 'FAIL'
    report[prof] = {'workflow': wf, 'status': status,
                    'seconds': round(time.time() - t0),
                    'tail': out[-1500:]}
    print(f'    {status} ({report[prof]["seconds"]}s) {out.splitlines()[-1][:160] if out else ""}', flush=True)
    json.dump(report, open('/tmp/lane_report.json', 'w'), indent=1)

npass = sum(1 for v in report.values() if v['status'] == 'PASS')
print(f'\n{npass}/{len(report)} lanes passed; report at /tmp/lane_report.json')
