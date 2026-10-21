# Deploys the wan-2-2-animate-gv endpoint with nothing but RUNPOD_API_KEY:
#   python infra/runpod-animate/deploy.py
# No image of our own: a stock PyTorch image runs SETUP, which installs Wan 2.2
# and its deps into a venv on the network volume once; handler.py fetches the
# weights there on its first job. Re-running is safe: things are reused by name.
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = 'https://rest.runpod.io/v1'
KEY = os.environ.get('RUNPOD_API_KEY', '')
UA = {'User-Agent': 'ai-model-chat-deploy/1.0'}
IMAGE = 'runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2204'
DC = 'EU-RO-1'
GPUS = ['NVIDIA RTX PRO 6000 Blackwell Server Edition',
        'NVIDIA RTX PRO 6000 Blackwell Workstation Edition', 'NVIDIA H100 80GB HBM3']
# The worker takes jobs at once; the first job does the install, because RunPod
# exits a worker that spends too long starting up.
SETUP = r'''
set -e
V=/runpod-volume/animate
mkdir -p $V $V/tmp
exec 9>$V/setup.lock
flock 9
if [ ! -f $V/.env-ready ]; then
rm -rf $V/Wan2.2 $V/venv
git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git $V/Wan2.2
python3 -m venv --system-site-packages $V/venv
cat $V/Wan2.2/requirements.txt $V/Wan2.2/requirements_animate.txt | grep -vi -e flash_attn -e '^torch' > /tmp/req.txt
export TMPDIR=$V/tmp PIP_CACHE_DIR=$V/pipcache SAM2_BUILD_CUDA=0
$V/venv/bin/pip install -q -r /tmp/req.txt requests huggingface_hub
touch $V/.env-ready
fi
# A prebuilt flash-attn for this torch and Python; compiling one takes an hour.
if [ ! -f $V/.fa-ready ]; then
  set -- $($V/venv/bin/python -c "import sys, torch; print(f'cp{sys.version_info[0]}{sys.version_info[1]}', str(torch._C._GLIBCXX_USE_CXX11_ABI).upper())")
  $V/venv/bin/pip install -q "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.8cxx11abi$2-$1-$1-linux_x86_64.whl" && touch $V/.fa-ready
fi
'''
BOOT = r'''
import json, os, subprocess, tempfile, runpod
V = '/runpod-volume/animate'
def tail(r): return ((r.stdout or '') + (r.stderr or ''))[-4000:]
def handler(job):
    if not (os.path.exists(V + '/.env-ready') and os.path.exists(V + '/.fa-ready')):
        r = subprocess.run(['bash', '/setup.sh'], capture_output=True, text=True)
        if not os.path.exists(V + '/.env-ready'):
            return {'error': 'setup failed: ' + tail(r)}
    d = tempfile.mkdtemp()
    src, out = d + '/in.json', d + '/out.json'
    with open(src, 'w') as f: json.dump(job['input'], f)
    r = subprocess.run([V + '/venv/bin/python', '-u', '/handler.py', src, out],
                       cwd=V + '/Wan2.2', capture_output=True, text=True)
    if not os.path.exists(out): return {'error': tail(r)}
    with open(out) as f: return json.load(f)
runpod.serverless.start({'handler': handler})
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


vol = find('/networkvolumes', 'wan-animate') or call('POST', '/networkvolumes', {
    'name': 'wan-animate', 'size': 150, 'dataCenterId': DC})
print('volume', vol['id'], flush=True)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'handler.py'), 'rb') as f:
    handler = base64.b64encode(f.read()).decode()
body = {'name': 'wan-animate-worker', 'imageName': IMAGE, 'isServerless': True,
        'containerDiskInGb': 40,
        'env': {'ANIMATE_HANDLER': handler, 'ANIMATE_SETUP': base64.b64encode(SETUP.encode()).decode(),
                'ANIMATE_BOOT': base64.b64encode(BOOT.encode()).decode(),
                'HF_HOME': '/runpod-volume/animate/hf'},
        'dockerStartCmd': ['bash', '-c', 'echo "$ANIMATE_SETUP" | base64 -d > /setup.sh; '
                           'echo "$ANIMATE_HANDLER" | base64 -d > /handler.py; '
                           'echo "$ANIMATE_BOOT" | base64 -d > /boot.py; '
                           'export DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC; apt-get update -qq && apt-get install -y -qq ffmpeg git > /dev/null; '
                           'pip install -q runpod; exec python3 -u /boot.py']}
tpl = find('/templates', 'wan-animate-worker')
if tpl:
    body.pop('isServerless')
    tpl = call('PATCH', f'/templates/{tpl["id"]}', body)
else:
    tpl = call('POST', '/templates', body)
print('template', tpl['id'], flush=True)
# The first job installs and downloads (~75 GB), so the ceiling is generous.
settings = {'templateId': tpl['id'], 'networkVolumeId': vol['id'], 'dataCenterIds': [DC],
            'computeType': 'GPU', 'gpuTypeIds': GPUS, 'gpuCount': 1, 'workersMin': 1,
            'workersMax': 2, 'idleTimeout': 300, 'flashboot': True,
            'executionTimeoutMs': 2 * 3600 * 1000}
ep = find('/endpoints', 'wan-animate')
ep = call('PATCH', f'/endpoints/{ep["id"]}', {k: v for k, v in settings.items() if k != 'computeType'}) \
    if ep else call('POST', '/endpoints', dict(settings, name='wan-animate'))
print('ENDPOINT', ep['id'])
