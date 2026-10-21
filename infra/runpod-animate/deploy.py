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
SETUP = r'''
V=/runpod-volume/animate
mkdir -p $V $V/tmp
LOG=$V/setup.log
{
apt-get update -qq && apt-get install -y -qq ffmpeg git > /dev/null
if [ ! -f $V/.env-ready ]; then
  set -e
  rm -rf $V/Wan2.2 $V/venv
  git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git $V/Wan2.2
  python3 -m venv --system-site-packages $V/venv
  cat $V/Wan2.2/requirements.txt $V/Wan2.2/requirements_animate.txt | grep -vi -e flash_attn -e '^torch' > /tmp/req.txt
  SAM2_BUILD_CUDA=0 TMPDIR=$V/tmp PIP_CACHE_DIR=$V/pipcache $V/venv/bin/pip install -r /tmp/req.txt runpod requests huggingface_hub
  set +e
  TMPDIR=$V/tmp PIP_CACHE_DIR=$V/pipcache $V/venv/bin/pip install flash-attn --no-build-isolation
  touch $V/.env-ready
fi
df -h / /runpod-volume
} > $LOG 2>&1
echo "$ANIMATE_HANDLER" | base64 -d > /handler.py
if [ -f $V/.env-ready ]; then cd $V/Wan2.2 && exec $V/venv/bin/python -u /handler.py; fi
# Setup failed: answer every job with the log, so the error is readable from the API.
pip install -q runpod
exec python3 -u -c "
import runpod
runpod.serverless.start({'handler': lambda j: {'error': open('$LOG').read()[-4000:]}})"
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
                'HF_HOME': '/runpod-volume/animate/hf'},
        'dockerStartCmd': ['bash', '-c', 'echo "$ANIMATE_SETUP" | base64 -d > /setup.sh && exec bash /setup.sh']}
tpl = find('/templates', 'wan-animate-worker')
if tpl:
    body.pop('isServerless')
    tpl = call('PATCH', f'/templates/{tpl["id"]}', body)
else:
    tpl = call('POST', '/templates', body)
print('template', tpl['id'], flush=True)
# The first job installs and downloads (~75 GB), so the ceiling is generous.
settings = {'templateId': tpl['id'], 'networkVolumeId': vol['id'], 'dataCenterIds': [DC],
            'computeType': 'GPU', 'gpuTypeIds': GPUS, 'gpuCount': 1, 'workersMin': 0,
            'workersMax': 2, 'idleTimeout': 300, 'flashboot': True,
            'executionTimeoutMs': 2 * 3600 * 1000}
ep = find('/endpoints', 'wan-animate')
ep = call('PATCH', f'/endpoints/{ep["id"]}', {k: v for k, v in settings.items() if k != 'computeType'}) \
    if ep else call('POST', '/endpoints', dict(settings, name='wan-animate'))
print('ENDPOINT', ep['id'])
