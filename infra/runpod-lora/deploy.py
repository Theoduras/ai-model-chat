# Deploys the char-lora endpoint (character LoRA training and LoRA stills)
# with nothing but RUNPOD_API_KEY:
#   python infra/runpod-lora/deploy.py [datacenter]    volume, template, endpoint
#   python infra/runpod-lora/deploy.py test <endpoint>  one still, no LoRA
#
# No image of our own: stock ostris/aitoolkit, with handler.py installed by the
# start command. The base weights (~60 GB) land on a network volume on the first
# job and are kept, so only that first job waits for them. Re-running is safe:
# whatever already exists by name is reused.
import base64
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'runpod-h3'))
from deploy import KEY, call, find  # noqa: E402  (h3's REST helpers)

IMAGE = 'ostris/aitoolkit:latest'
# 80 GB and up: a still keeps both 14B experts in bf16 on the card.
GPUS = ['NVIDIA RTX PRO 6000 Blackwell Server Edition',
        'NVIDIA RTX PRO 6000 Blackwell Workstation Edition', 'NVIDIA H100 80GB HBM3']
DC = 'EU-RO-1'
VOLUME_GB = 150


def start_cmd():
    return ['bash', '-c', 'echo "$LORA_HANDLER" | base64 -d > /char_lora.py; '
            'pip install -q --break-system-packages runpod; exec python3 -u /char_lora.py']


def deploy(dc):
    vol = find('/networkvolumes', 'char-lora') or call('POST', '/networkvolumes', {
        'name': 'char-lora', 'size': VOLUME_GB, 'dataCenterId': dc})
    print('volume', vol['id'], vol.get('dataCenterId'), flush=True)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'handler.py'), 'rb') as f:
        handler = base64.b64encode(f.read()).decode()
    body = {'name': 'char-lora-worker', 'imageName': IMAGE, 'isServerless': True,
            'containerDiskInGb': 60,
            'env': {'LORA_HANDLER': handler, 'HF_HOME': '/runpod-volume/hf'},
            'dockerStartCmd': start_cmd()}
    tpl = find('/templates', 'char-lora-worker')
    if tpl:
        body.pop('isServerless')
        tpl = call('PATCH', f'/templates/{tpl["id"]}', body)
    else:
        tpl = call('POST', '/templates', body)
    print('template', tpl['id'], flush=True)
    # A training run takes about an hour; three is the ceiling before RunPod
    # calls it timed out and the app refunds it.
    settings = {'templateId': tpl['id'], 'networkVolumeId': vol['id'], 'dataCenterIds': [vol.get('dataCenterId') or dc],
                'computeType': 'GPU', 'gpuTypeIds': GPUS, 'gpuCount': 1, 'workersMin': 0,
                'workersMax': 2, 'idleTimeout': 120, 'flashboot': True,
                'executionTimeoutMs': 3 * 3600 * 1000, 'allowedCudaVersions': ['13.0']}
    ep = find('/endpoints', 'char-lora')
    ep = call('PATCH', f'/endpoints/{ep["id"]}', {k: v for k, v in settings.items()
                                                 if k != 'computeType'}) if ep else call(
        'POST', '/endpoints', dict(settings, name='char-lora'))
    print('ENDPOINT', ep['id'])


def test(endpoint):
    os.environ['RUNPOD_LORA_ENDPOINT'] = endpoint
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import imagegen
    rp = imagegen.RunPodProvider(KEY)
    start = time.time()
    job, res = rp.submit_image({'model': imagegen.CHAR_LORA_IMAGE_MODEL, 'seed': 7, 'aspect': '2:3',
                                'prompt': 'Photo of a woman in a red coat on a rainy street at night.'})
    print('job', job, flush=True)
    while res.status == 'running':
        time.sleep(15)
        res = rp.poll(job)
        print(f'  {time.time() - start:.0f}s {res.status}', flush=True)
    if res.status != 'done':
        sys.exit(f'test failed: {res.error}')
    out = os.path.abspath('char-lora-test.png')
    with open(out, 'wb') as f:
        f.write(base64.b64decode(res.urls[0].split(',', 1)[1]))
    print('still', out, f'in {time.time() - start:.0f}s')


if __name__ == '__main__':
    if sys.argv[1:2] == ['test']:
        test(sys.argv[2])
    else:
        deploy(sys.argv[1] if len(sys.argv) > 1 else DC)
