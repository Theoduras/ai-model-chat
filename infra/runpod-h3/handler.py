# Wraps worker-comfyui's handler: any LoraLoaderModelOnly whose lora_name is a
# link is downloaded once per worker and swapped for the file name, so the app
# can hand Civitai LoRAs over by URL, as it does for wan-2-2-gv.
import hashlib
import os
import urllib.request

import runpod

import comfy_handler

LORAS = '/comfyui/models/loras'
MODELS = ('/comfyui/models', '/runpod-volume/models')
# The worker's own H3 files win over the names the app sends, so which weights
# deploy.py downloaded (int8, or the smaller nvfp4/w6a8) never fails a job.
SWAP = {'UNETLoader': ('unet_name', ('unet', 'diffusion_models'), 'minimax_h3_fl2va'),
        'CLIPLoader': ('clip_name', ('clip', 'text_encoders'), 'qwen3vl_32b_minimax_h3')}


def present(folders, prefix):
    for root in MODELS:
        for folder in folders:
            try:
                names = sorted(os.listdir(os.path.join(root, folder)))
            except OSError:
                continue
            for name in names:
                if name.startswith(prefix) and name.endswith('.safetensors'):
                    yield name


def fetch(url):
    name = 'h3_' + hashlib.sha1(url.split('?')[0].encode()).hexdigest()[:16] + '.safetensors'
    path = os.path.join(LORAS, name)
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=900) as r, open(path + '.part', 'wb') as f:
            while chunk := r.read(1 << 22):
                f.write(chunk)
        os.replace(path + '.part', path)
    return name


def handler(job):
    for node in ((job.get('input') or {}).get('workflow') or {}).values():
        inputs = node.get('inputs') or {}
        if node.get('class_type') == 'LoraLoaderModelOnly' and str(inputs.get('lora_name', '')).startswith('http'):
            inputs['lora_name'] = fetch(inputs['lora_name'])
        if node.get('class_type') in SWAP:
            key, folders, prefix = SWAP[node['class_type']]
            have = list(present(folders, prefix))
            if have and inputs.get(key) not in have:
                inputs[key] = have[0]
    return comfy_handler.handler(job)


if __name__ == '__main__':
    runpod.serverless.start({'handler': handler})
