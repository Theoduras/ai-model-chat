# Deploys our RunPod workers to Vast Serverless with nothing but VAST_API_KEY:
#   python infra/vast/deploy.py [name ...]     templates, endpoints, workergroups
#   python infra/vast/deploy.py test <name>    one job through imagegen.VastProvider
# Names: wan-2-2-gv, h3-gv, wan-2-2-char. Each worker runs the same handler it
# runs on RunPod; sitecustomize.py turns it into a local job server and
# worker.py is the PyWorker in front. Re-running updates whatever exists by name.
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(HERE, '..', 'runpod-h3'))
os.environ.setdefault('RUNPOD_API_KEY', '-')
import deploy as h3  # noqa: E402  (FILES, HF, GET)

API = 'https://console.vast.ai/api/v0'
KEY = os.environ.get('VAST_API_KEY', '')


# The int8 set the live RunPod template runs and imagegen.h3_payload names:
# worker-comfyui 5.10.0's ComfyUI cannot load the smaller w6a8 file h3.FILES lists.
H3_FILES = [
    ('unet', 'diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors'),
    ('clip', 'text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors'),
    ('vae', 'vae/minimax_h3_video_vae_int8_convrot.safetensors'),
    ('vae', 'vae/minimax_h3_audio_vae_fp32.safetensors'),
    ('loras', 'loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors'),
]

WORKERS = {
    'wan-2-2-gv': {
        'image': 'ghcr.io/theoduras/vast-gv:latest', 'disk': 150,
        'gpus': 'gpu_name in [RTX_6000Ada,L40,L40S,RTX_PRO_6000_S,RTX_PRO_6000_WS] cuda_max_good>=12.8', 'env': {}},
    'h3-gv': {
        'image': h3.IMAGE, 'disk': 120,
        'gpus': 'gpu_name in [RTX_PRO_6000_S,RTX_PRO_6000_WS]', 'env': {'H3_FILES': json.dumps(H3_FILES, separators=(',', ':')), 'H3_HF': h3.HF}},
    'wan-2-2-char': {
        'image': 'ostris/aitoolkit:latest', 'disk': 300,
        'gpus': 'gpu_name in [RTX_PRO_6000_S,RTX_PRO_6000_WS,H100_SXM,H100_NVL]', 'env': {'HF_HOME': '/workspace/hf', 'MODELS_PATH': '/workspace/models'}},
}

# Scripts come from this repo (public) at VJ_REF. Truncating the log matters:
# a resumed cold worker must not read the last boot's "ready" line before its
# handler is up again.
ONSTART = ('mkdir -p /opt/vast_jobs/runpod-h3 /opt/vast_jobs/runpod-lora /opt/vast_jobs/start; cd /opt/vast_jobs; '
           'R=https://raw.githubusercontent.com/Theoduras/ai-model-chat/$VJ_REF/infra; '
           'for f in vast/sitecustomize.py vast/worker.py vast/preload.py vast/start/$VJ_NAME.sh runpod-gv-patch.py '
           'runpod-h3/handler.py runpod-h3/deploy.py runpod-lora/handler.py; do '
           'curl -fsSL $R/$f -o ${f#vast/}; done; : > /var/log/vast-jobs.log; '
           '(pip install -q --target /opt/pw vastai >/var/log/pyworker.log 2>&1; '
           'PYTHONPATH=/opt/pw nohup python3 worker.py >>/var/log/pyworker.log 2>&1 &); '
           'cd /; PYTHONPATH=/opt/vast_jobs nohup bash /opt/vast_jobs/start/$VJ_NAME.sh '
           '>>/var/log/vast-jobs.log 2>&1 & tail -F /var/log/vast-jobs.log /var/log/pyworker.log &')


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Bearer {KEY}',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        sys.exit(f'{method} {path}: {e.code} {e.read().decode()[:500]}')


def deploy(name):
    w = WORKERS[name]
    env = dict(w['env'], VJ_NAME=name, VJ_REF=os.getenv('VJ_REF', 'develop'), WORKER_PORT='3000',
               VJ_APP_URL=os.getenv('VJ_APP_URL', ''), VJ_LORA_KEY=os.getenv('VJ_LORA_KEY', ''))
    tpl = call('POST', '/template/', {
        'name': f'{name}-worker', 'image': w['image'], 'runtype': 'ssh', 'ssh_direct': True,
        'env': ' '.join(f"-e {k}='{v}'" for k, v in env.items()) + ' -p 3000:3000',
        'onstart': ONSTART, 'recommended_disk_space': w['disk']})
    tpl_hash = (tpl.get('template') or tpl).get('hash_id')
    print(name, 'template', tpl_hash, flush=True)
    eps = call('GET', '/endptjobs/').get('results') or []
    ep = next((e for e in eps if e.get('endpoint_name') == name), None)
    scale = {'min_load': 0, 'target_util': 0.9, 'cold_mult': 1, 'cold_workers': 1, 'max_workers': 2}
    if ep:
        call('PUT', f'/endptjobs/{ep["id"]}/', dict(scale, endpoint_name=name))
    else:
        ep = {'id': call('POST', '/endptjobs/', dict(scale, endpoint_name=name))['result']}
    groups = [g for g in call('GET', '/autojobs/').get('results') or []
              if g.get('endpoint_id') == ep['id']]
    group = {'endpoint_id': ep['id'], 'endpoint_name': name, 'template_hash': tpl_hash,
             'search_params': f"{w['gpus']} rentable=true verified=true disk_space>={w['disk']}",
             'min_load': 0, 'target_util': 0.9, 'cold_mult': 1, 'cold_workers': 1,
             'max_workers': 2, 'test_workers': 1}
    for g in groups:
        call('PUT', f'/autojobs/{g["id"]}/', group)
    if not groups:
        call('POST', '/autojobs/', group)
    print(name, 'endpoint', ep['id'], flush=True)


def test(name):
    """One smallest, fastest job: a 480p clip at the model's shortest length,
    or a 1:1 still. Prints a SMOKE line either way."""
    os.environ.setdefault('VAST_API_KEY', KEY or 'x')
    sys.path.insert(0, ROOT)
    import imagegen
    vp = imagegen.VastProvider(os.environ['VAST_API_KEY'])
    start = time.time()
    try:
        if name == imagegen.CHAR_LORA_IMAGE_MODEL:
            job, res = vp.submit_image({'model': name, 'seed': 7, 'aspect': '1:1',
                                        'prompt': 'Photo of a red apple on a table.'})
        else:
            job, res = vp.submit_video({
                'model': name, 'seconds': 1, 'resolution': '480p', 'seed': 7, 'aspect': '9:16',
                'reference_b64': base64.b64encode(h3.still_png()).decode(),
                'reference_mime': 'image/png', 'prompt': 'Slow camera push in on soft light.'})
        print(f'SMOKE {name} submitted after {time.time() - start:.0f}s', flush=True)
        while res.status == 'running':
            time.sleep(10)
            res = vp.poll(job)
        if res.status != 'done':
            raise RuntimeError(res.error)
        url = res.urls[0]
        size = len(base64.b64decode(url.split(',', 1)[1])) if url.startswith('data:') else \
            len(urllib.request.urlopen(url, timeout=120).read())
        print(f'SMOKE {name} OK {size >> 10} KB in {time.time() - start:.0f}s', flush=True)
    except Exception as e:
        print(f'SMOKE {name} FAIL after {time.time() - start:.0f}s: {e}', flush=True)


if __name__ == '__main__':
    if sys.argv[1:2] == ['test']:
        import threading
        names = list(WORKERS) if sys.argv[2] == 'all' else sys.argv[2:]
        threads = [threading.Thread(target=test, args=(n,)) for n in names]
        [t.start() for t in threads]
        [t.join() for t in threads]
    else:
        for n in sys.argv[1:] or WORKERS:
            deploy(n)
