# Deploys the char-caption endpoint (explicit image captions for LoRA picks)
# with nothing but RUNPOD_API_KEY:
#   python infra/runpod-caption/deploy.py [datacenter]      volume, template, endpoint
#   python infra/runpod-caption/deploy.py test <endpoint> <image.jpg>
#
# Stock PyTorch image, handler.py installed by the start command, weights
# (~17 GB) cached on a network volume by the first job.
import base64
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'runpod-h3'))
from deploy import KEY, call, find  # noqa: E402  (h3's REST helpers)

IMAGE = 'runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04'
# 24-48 GB cards torch 2.4 / CUDA 12.4 runs: no Blackwell (5090, PRO 6000).
GPUS = ['NVIDIA GeForce RTX 4090', 'NVIDIA RTX A5000', 'NVIDIA L4', 'NVIDIA RTX A6000',
        'NVIDIA A40', 'NVIDIA L40S', 'NVIDIA RTX 6000 Ada Generation']
DC = 'EU-RO-1'
VOLUME_GB = 30


def deploy(dc):
    vol = find('/networkvolumes', 'char-caption') or call('POST', '/networkvolumes', {
        'name': 'char-caption', 'size': VOLUME_GB, 'dataCenterId': dc})
    print('volume', vol['id'], vol.get('dataCenterId'), flush=True)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'handler.py'), 'rb') as f:
        handler = base64.b64encode(f.read()).decode()
    body = {'name': 'char-caption-worker', 'imageName': IMAGE, 'isServerless': True,
            'containerDiskInGb': 40,
            'env': {'CAPTION_HANDLER': handler, 'HF_HOME': '/runpod-volume/hf'},
            'dockerStartCmd': ['bash', '-c', 'echo "$CAPTION_HANDLER" | base64 -d > /caption.py; '
                               'pip install -q runpod transformers==4.49.0 accelerate==1.4.0 pillow; '
                               'exec python3 -u /caption.py']}
    tpl = find('/templates', 'char-caption-worker')
    if tpl:
        body.pop('isServerless')
        tpl = call('PATCH', f'/templates/{tpl["id"]}', body)
    else:
        tpl = call('POST', '/templates', body)
    print('template', tpl['id'], flush=True)
    settings = {'templateId': tpl['id'], 'networkVolumeId': vol['id'],
                'dataCenterIds': [vol.get('dataCenterId') or dc], 'computeType': 'GPU',
                'gpuTypeIds': GPUS, 'gpuCount': 1, 'workersMin': 0, 'workersMax': 1,
                'idleTimeout': 60, 'flashboot': True, 'executionTimeoutMs': 600 * 1000}
    ep = find('/endpoints', 'char-caption')
    ep = call('PATCH', f'/endpoints/{ep["id"]}', {k: v for k, v in settings.items()
                                                 if k != 'computeType'}) if ep else call(
        'POST', '/endpoints', dict(settings, name='char-caption'))
    print('ENDPOINT', ep['id'])


def test(endpoint, image):
    os.environ['RUNPOD_CAPTION_ENDPOINT'] = endpoint
    os.environ.setdefault('RUNPOD_API_KEY', KEY)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import imagegen
    start = time.time()
    with open(image, 'rb') as f:
        print(imagegen.read_frame(f.read(), wait=900), f'in {time.time() - start:.0f}s')


if __name__ == '__main__':
    if sys.argv[1:2] == ['test']:
        test(sys.argv[2], sys.argv[3])
    else:
        deploy(sys.argv[1] if len(sys.argv) > 1 else DC)
