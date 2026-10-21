# Deploys the wan-2-2-animate-gv endpoint. Build and push the image first:
#   docker build -t <you>/wan-animate:1 infra/runpod-animate && docker push <you>/wan-animate:1
#   ANIMATE_IMAGE=<you>/wan-animate:1 RUNPOD_API_KEY=... python infra/runpod-animate/deploy.py
# Prints the endpoint id: set it as RUNPOD_ANIMATE_ENDPOINT on Vercel and Cloud Run.
# Re-running is safe: whatever already exists by name is reused.
import json
import os
import sys
import urllib.error
import urllib.request

API = 'https://rest.runpod.io/v1'
KEY = os.environ['RUNPOD_API_KEY']
IMAGE = os.environ['ANIMATE_IMAGE']
UA = {'User-Agent': 'ai-model-chat-deploy/1.0'}
DC = os.getenv('ANIMATE_DC', 'EU-RO-1')
# 14B with offload fits 48 GB at 480p; 80 GB is the headroom for 720p.
GPUS = ['NVIDIA A100 80GB PCIe', 'NVIDIA H100 80GB HBM3', 'NVIDIA RTX 6000 Ada Generation']


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
    'name': 'wan-animate', 'size': 120, 'dataCenterId': DC})
tpl = find('/templates', 'wan-animate') or call('POST', '/templates', {
    'name': 'wan-animate', 'imageName': IMAGE, 'isServerless': True,
    'containerDiskInGb': 40, 'env': {}})
ep = find('/endpoints', 'wan-animate') or call('POST', '/endpoints', {
    'name': 'wan-animate', 'templateId': tpl['id'], 'networkVolumeId': vol['id'],
    'dataCenterIds': [DC], 'gpuTypeIds': GPUS, 'workersMin': 0, 'workersMax': 2,
    'idleTimeout': 300, 'flashboot': True, 'executionTimeoutMs': 1800000})
print('RUNPOD_ANIMATE_ENDPOINT =', ep['id'])
