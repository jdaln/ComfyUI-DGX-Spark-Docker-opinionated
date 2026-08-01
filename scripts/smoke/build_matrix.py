import json, os, glob, re, subprocess

m = json.load(open('asset-profiles.json'))
groups = m['groups']
prof_files = {}
for name, gl in m['profiles'].items():
    s = set()
    for g in gl:
        for e in groups[g]:
            d = e.get('dest') or e.get('link')
            if d: s.add(os.path.basename(d))
    prof_files[name] = s

tpl_dir = subprocess.check_output(['docker','exec','comfyui','python3','-c',
  'import comfyui_workflow_templates_json,os;print(os.path.dirname(comfyui_workflow_templates_json.__file__))']).decode().strip()+'/templates'
names = subprocess.check_output(['docker','exec','comfyui','bash','-c',f'ls {tpl_dir}/*.json']).decode().split()

def refs_from(d):
    out=set()
    if not isinstance(d, dict): return out
    def scan(nodes):
        for n in nodes:
            if not isinstance(n, dict): continue
            for w in n.get('widgets_values') or []:
                if isinstance(w,str) and re.search(r'\.(safetensors|gguf|sft|onnx|pt|pth)$', w):
                    out.add(os.path.basename(w))
    scan(d.get('nodes') or [])
    for sg in (d.get('definitions') or {}).get('subgraphs') or []:
        if isinstance(sg, dict): scan(sg.get('nodes') or [])
    return out

wf_refs = {}
for n in names:
    try:
        data = json.loads(subprocess.check_output(['docker','exec','comfyui','cat',n]).decode())
    except Exception:
        continue
    r = refs_from(data)
    if r: wf_refs['tpl:'+os.path.basename(n)[:-5]] = r
for f in glob.glob('custom_nodes/*/example_workflows/*.json')+glob.glob('ComfyUI/blueprints/*.json'):
    try: data=json.load(open(f))
    except Exception: continue
    r = refs_from(data)
    if r: wf_refs[('bp:' if 'blueprints' in f else 'cn:')+os.path.basename(f)[:-5]] = r

all_manifest = set().union(*prof_files.values())
print(f'{len(wf_refs)} workflows with model refs')
matrix = {}
for p, files in prof_files.items():
    best = []
    for w, refs in wf_refs.items():
        if refs & files and refs <= all_manifest:
            cov = len(refs & files)/len(refs)
            best.append((cov, w))
    best.sort(reverse=True)
    matrix[p] = [w for c,w in best if c==best[0][0]][:3] if best else []
    print(f'{p:46s} -> {matrix[p]}')
json.dump(matrix, open('tmp/test_matrix.json','w'), indent=1)
