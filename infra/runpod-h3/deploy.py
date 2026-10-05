# Deploys the h3-gv endpoint with nothing but RUNPOD_API_KEY, then tests it:
#   python infra/runpod-h3/deploy.py [datacenter]    volume, weights, endpoint
#   python infra/runpod-h3/deploy.py test <endpoint>  one 5s 480p clip
#
# No image of our own: the weights go onto a network volume once (a CPU pod
# downloads them), and the endpoint runs stock worker-comfyui from that volume
# with handler.py installed by the start command. Re-running is safe: whatever
# already exists by name is reused, and files already on the volume are kept.
import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

API = 'https://rest.runpod.io/v1'
KEY = os.environ['RUNPOD_API_KEY']
# Cloudflare in front of rest.runpod.io refuses urllib's default User-Agent (1010).
UA = {'User-Agent': 'ai-model-chat-deploy/1.0'}
IMAGE = 'runpod/worker-comfyui:5.10.0-base'
HF = 'https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main'
# worker-comfyui reads /runpod-volume/models/{unet,clip,vae,loras}; ComfyUI
# maps unet and clip onto diffusion_models and text_encoders.
FILES = [
    ('unet', 'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors'),
    ('clip', 'text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors'),
    ('vae', 'vae/minimax_h3_video_vae_int8_convrot.safetensors'),
    ('vae', 'vae/minimax_h3_audio_vae_fp32.safetensors'),
    ('loras', 'loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors'),
]
GPUS = ['NVIDIA H100 80GB HBM3', 'NVIDIA H100 PCIe', 'NVIDIA H100 NVL',
        'NVIDIA A100-SXM4-80GB', 'NVIDIA A100 80GB PCIe']

# Runs on the download pod. The pod is never left to exit: RunPod restarts an
# exited container, so a finished or failed download would start over forever.
# It reports through status.json on port 8000 instead, and a file only gets its
# real name once its size matches what the server said, so a cut download is
# resumed on the next run rather than taken for a finished one.
FETCH = r'''
import http.server, json, os, threading, urllib.request
FILES, HF = json.loads(os.environ['H3_FILES']), os.environ['H3_HF']
os.makedirs('/tmp/www', exist_ok=True)
def say(**s):
    with open('/tmp/www/status.tmp', 'w') as f: json.dump(s, f)
    os.replace('/tmp/www/status.tmp', '/tmp/www/status.json')
def get(url, path):
    part = path + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url, headers={'Range': f'bytes={have}-'} if have else {})
    with urllib.request.urlopen(req, timeout=120) as r:
        if have and r.status != 206: have = 0
        total = have + int(r.headers['Content-Length'])
        with open(part, 'ab' if have else 'wb') as f:
            while chunk := r.read(1 << 24): f.write(chunk)
    if os.path.getsize(part) != total:
        raise IOError(f'{os.path.basename(path)}: {os.path.getsize(part)} of {total} bytes')
    os.replace(part, path)
def run():
    try:
        for i, (folder, src) in enumerate(FILES):
            d = f'/workspace/models/{folder}'
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, src.split('/')[-1])
            for attempt in range(5):
                if os.path.exists(path): break
                say(state='downloading', file=src, n=i + 1, of=len(FILES), attempt=attempt + 1)
                try: get(f'{HF}/{src}', path)
                except Exception as e: err = repr(e)
            if not os.path.exists(path): raise IOError(f'{src}: {err}')
        say(state='done')
    except Exception as e:
        say(state='failed', error=str(e))
say(state='starting')
threading.Thread(target=run, daemon=True).start()
http.server.ThreadingHTTPServer(('', 8000), lambda *a: http.server.SimpleHTTPRequestHandler(
    *a, directory='/tmp/www')).serve_forever()
'''


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Bearer {KEY}',
                                          'Content-Type': 'application/json', **UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f'{method} {path}: {e.code} {e.read().decode()[:500]}')


def find(path, name):
    return next((x for x in call('GET', path) if x.get('name') == name), None)


def download(vol, dc):
    pod = call('POST', '/pods', {
        'name': 'h3-download', 'computeType': 'CPU', 'cpuFlavorIds': ['cpu3c'], 'vcpuCount': 2,
        'imageName': 'python:3.12-slim', 'networkVolumeId': vol['id'], 'dataCenterIds': [dc],
        'containerDiskInGb': 5, 'ports': ['8000/http'],
        'env': {'H3_FILES': json.dumps(FILES), 'H3_HF': HF,
                'H3_FETCH': base64.b64encode(FETCH.encode()).decode()},
        'dockerStartCmd': ['bash', '-c', 'echo "$H3_FETCH" | base64 -d > /fetch.py && exec python3 -u /fetch.py']})
    print('download pod', pod['id'], '(~52 GB)', flush=True)
    status, deadline, last = {}, time.time() + 4 * 3600, None
    try:
        while status.get('state') not in ('done', 'failed'):
            if time.time() > deadline:
                sys.exit('download did not finish in 4 hours')
            time.sleep(30)
            try:
                with urllib.request.urlopen(urllib.request.Request(
                        f'https://{pod["id"]}-8000.proxy.runpod.net/status.json', headers=UA),
                        timeout=20) as r:
                    status = json.loads(r.read())
            except Exception:
                continue
            line = json.dumps(status)
            if line != last:
                print(' ', line, flush=True)
                last = line
    finally:
        call('DELETE', f'/pods/{pod["id"]}')
    if status['state'] == 'failed':
        sys.exit(f'download failed: {status.get("error")}')


# Runs on each worker before ComfyUI: fetches any weight the container disk
# lacks, all five at once, resuming a cut file. Python, because the stock
# image has no wget, and a missing tool there failed silently.
GET = r'''
import json, os, sys, threading, time, urllib.request
FILES, HF = json.loads(os.environ['H3_FILES']), os.environ['H3_HF']
done, size = {}, {}
def report():
    while True:
        time.sleep(10)
        have, total = sum(done.values()), sum(size.values())
        left = [f'{n} {100 * done[n] // max(1, size[n])}%' for n in size if done[n] < size[n]]
        print(f'h3 weights: {have / 1e9:.1f} / {total / 1e9:.1f} GB ({100 * have // max(1, total)}%)',
              ', '.join(left), flush=True)
def get(folder, src):
    d = f'/comfyui/models/{folder}'
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, src.split('/')[-1])
    for attempt in range(5):
        if os.path.exists(path): return
        name = src.split('/')[-1].split('.')[0]
        part = path + '.part'
        have = os.path.getsize(part) if os.path.exists(part) else 0
        try:
            req = urllib.request.Request(f'{HF}/{src}', headers={'Range': f'bytes={have}-'} if have else {})
            with urllib.request.urlopen(req, timeout=120) as r:
                if have and r.status != 206: have = 0
                total = have + int(r.headers['Content-Length'])
                size[name], done[name] = total, have
                with open(part, 'ab' if have else 'wb') as f:
                    while chunk := r.read(1 << 24):
                        f.write(chunk)
                        done[name] += len(chunk)
            if os.path.getsize(part) == total: os.replace(part, path)
        except Exception as e:
            print('h3 weights:', src, repr(e), flush=True)
threading.Thread(target=report, daemon=True).start()
ts = [threading.Thread(target=get, args=f) for f in FILES]
[t.start() for t in ts]; [t.join() for t in ts]
missing = [s for f, s in FILES if not os.path.exists(f'/comfyui/models/{f}/' + s.split('/')[-1])]
print('h3 weights missing:' if missing else 'h3 weights ready', missing or '', flush=True)
'''


def start_cmd():
    """Install handler.py, fetch the weights, then start worker-comfyui."""
    return ['bash', '-c', 'mv -n /handler.py /comfy_handler.py; '
            'echo "$H3_HANDLER" | base64 -d > /handler.py; '
            'echo "$H3_GET" | base64 -d > /h3_get.py; python3 -u /h3_get.py; exec /start.sh']


def deploy(dc=None):
    """No network volume: one pins the endpoint to its data centre. Each new
    worker downloads the ~52 GB itself, so any 80 GB GPU anywhere can serve."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'handler.py'), 'rb') as f:
        handler = base64.b64encode(f.read()).decode()
    body = {'name': 'h3-worker', 'imageName': IMAGE, 'isServerless': True, 'containerDiskInGb': 80,
            'env': {'H3_HANDLER': handler, 'H3_FILES': json.dumps(FILES), 'H3_HF': HF,
                    'H3_GET': base64.b64encode(GET.encode()).decode()},
            'dockerStartCmd': start_cmd()}
    tpl = find('/templates', 'h3-worker')
    if tpl:
        body.pop('isServerless')
        tpl = call('PATCH', f'/templates/{tpl["id"]}', body)
    else:
        tpl = call('POST', '/templates', body)
    print('template', tpl['id'], flush=True)

    settings = {'templateId': tpl['id'], 'networkVolumeIds': [],
                'computeType': 'GPU', 'gpuTypeIds': GPUS, 'gpuCount': 1, 'workersMin': 0,
                'workersMax': 2, 'idleTimeout': 300, 'flashboot': True,
                'executionTimeoutMs': 30 * 60 * 1000}
    if dc:
        settings['dataCenterIds'] = [dc]
    ep = find('/endpoints', 'h3-gv')
    # An update refuses computeType; it is fixed when the endpoint is made.
    ep = call('PATCH', f'/endpoints/{ep["id"]}', {k: v for k, v in settings.items()
                                                 if k != 'computeType'}) if ep else call(
        'POST', '/endpoints', dict(settings, name='h3-gv'))
    print('ENDPOINT', ep['id'], ep.get('networkVolumeIds'), ep.get('dataCenterIds'))


def still_png(w=480, h=832):
    """A plain gradient, so the test sends nothing that needs a photo."""
    rows = b''.join(b'\0' + bytes(c for x in range(w) for c in (x * 255 // w, y * 255 // h, 160))
                    for y in range(h))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def test(endpoint):
    """One clip through the app's own provider code, as the studio would send it."""
    os.environ['RUNPOD_H3_ENDPOINT'] = endpoint
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import imagegen
    rp = imagegen.RunPodProvider(KEY)
    start = time.time()
    job, res = rp.submit_video({
        'model': 'h3-gv', 'seconds': 5, 'resolution': '480p', 'seed': 7,
        'reference_b64': base64.b64encode(still_png()).decode(), 'reference_mime': 'image/png',
        'prompt': 'Slow camera push in on soft colored light, gentle ambient wind sound.'})
    print('job', job, flush=True)
    while res.status == 'running':
        time.sleep(15)
        res = rp.poll(job)
        print(f'  {time.time() - start:.0f}s {res.status}', flush=True)
    if res.status != 'done':
        sys.exit(f'test failed: {res.error}')
    url = res.urls[0]
    data = base64.b64decode(url.split(',', 1)[1]) if url.startswith('data:') else \
        urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
    out = os.path.abspath('h3-test.mp4')
    with open(out, 'wb') as f:
        f.write(data)
    req = urllib.request.Request('{}/status/{}'.format(*rp._endpoint(job)),
                                 headers={'Authorization': f'Bearer {KEY}', **UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = json.loads(r.read())
    print(f'clip {out} ({len(data) >> 10} KB) in {time.time() - start:.0f}s: '
          f'queued {t.get("delayTime", 0) / 1000:.0f}s, ran {t.get("executionTime", 0) / 1000:.0f}s')


if __name__ == '__main__':
    if sys.argv[1:2] == ['test']:
        test(sys.argv[2])
    else:
        deploy(sys.argv[1] if len(sys.argv) > 1 else None)
