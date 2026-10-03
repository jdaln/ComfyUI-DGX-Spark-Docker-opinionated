#!/usr/bin/env python3
"""Run all smoke lanes sequentially, write /tmp/lane_report.json as it goes.

Lanes run strictly one at a time. Before each lane it frees ComfyUI's models and
waits until available memory is back above LANE_MIN_AVAILABLE_GIB (default 85).
If it does not come back within 15 minutes the sweep stops rather than start a
lane on top of whatever is still holding memory. After a failed lane it also
interrupts the server and clears its queue, so a prompt that outlived its
timeout cannot keep running under the next lane. While a lane runs,
/system_stats is sampled every 10 s and the lowest available memory and lowest
free VRAM go into the report.
"""
import json, os, subprocess, sys, threading, time, urllib.request

PORT = 8188
MIN_AVAILABLE_GIB = float(os.environ.get('LANE_MIN_AVAILABLE_GIB', '85'))


def free_models():
    """Drop ComfyUI's cached models between lanes.

    ComfyUI keeps the last model resident so a rerun is fast. Lanes run one at a
    time, but nothing unloads between them, so a batch accumulates: finishing a
    40 GB video lane and starting one that needs a different checkpoint asks for
    both at once. On a DGX Spark that memory is shared with the host, and the
    box goes down rather than raising an OOM.
    """
    req = urllib.request.Request(
        f'http://127.0.0.1:{PORT}/free',
        data=json.dumps({'unload_models': True, 'free_memory': True}).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as exc:                      # never fail a lane over this
        print(f'    warning: could not free models: {exc}', flush=True)


def stop_running():
    """Interrupt whatever the server is executing and drop anything queued."""
    for path, body in (('/queue', {'clear': True}), ('/interrupt', {})):
        req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except Exception as exc:
            print(f'    warning: {path} failed: {exc}', flush=True)


def memory():
    """(available GiB, free VRAM GiB). Free VRAM counts the page cache as used;
    available does not, so available is the one to gate on."""
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/system_stats', timeout=30) as r:
        stats = json.loads(r.read())
    return stats['system']['ram_free'] / 2**30, stats['devices'][0]['vram_free'] / 2**30


def wait_for_memory(prof):
    deadline = time.time() + 900
    while True:
        try:
            available = memory()[0]
        except Exception as exc:
            print(f'    warning: /system_stats failed: {exc}', flush=True)
            available = 0.0
        if available >= MIN_AVAILABLE_GIB:
            return True
        if time.time() > deadline:
            print(f'STOP: {available:.0f} GiB available before {prof}, the gate is '
                  f'{MIN_AVAILABLE_GIB:.0f} GiB (LANE_MIN_AVAILABLE_GIB)', flush=True)
            return False
        time.sleep(15)


def sample(lows, done):
    while not done.is_set():
        try:
            available, vram = memory()
            lows['available'] = min(lows.get('available', available), available)
            lows['vram'] = min(lows.get('vram', vram), vram)
        except Exception:
            pass
        done.wait(10)


lanes = json.load(open('/tmp/lanes.json'))['lanes']
only = sys.argv[1:] if len(sys.argv) > 1 else None
report = {}
for lane in lanes:
    prof, wf = lane[0], lane[1]
    lane_subs = lane[2] if len(lane) > 2 else None
    if only and prof not in only:
        continue
    print(f'=== {prof} :: {wf}', flush=True)
    free_models()
    if not wait_for_memory(prof):
        break
    cmd = ['python3', '/tmp/wf_smoke.py', wf, '1800']
    if lane_subs:
        cmd.append(json.dumps(lane_subs))
    lows, done = {}, threading.Event()
    sampler = threading.Thread(target=sample, args=(lows, done), daemon=True)
    sampler.start()
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    done.set()
    sampler.join()
    out = (r.stdout + r.stderr).strip()
    status = 'PASS' if r.returncode == 0 else 'FAIL'
    if status == 'FAIL':
        stop_running()
    report[prof] = {'workflow': wf, 'status': status,
                    'seconds': round(time.time() - t0),
                    'lowest_available_gib': round(lows.get('available', 0), 1),
                    'lowest_free_vram_gib': round(lows.get('vram', 0), 1),
                    'tail': out[-1500:]}
    print(f'    {status} ({report[prof]["seconds"]}s, lowest {report[prof]["lowest_available_gib"]} GiB '
          f'available) {out.splitlines()[-1][:160] if out else ""}', flush=True)
    json.dump(report, open('/tmp/lane_report.json', 'w'), indent=1)

free_models()
json.dump(report, open('/tmp/lane_report.json', 'w'), indent=1)

npass = sum(1 for v in report.values() if v['status'] == 'PASS')
print(f'\n{npass}/{len(report)} lanes passed; report at /tmp/lane_report.json')
