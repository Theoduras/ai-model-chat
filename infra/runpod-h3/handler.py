# Wraps worker-comfyui's handler: any LoraLoaderModelOnly whose lora_name is a
# link is downloaded once per worker and swapped for the file name, so the app
# can hand Civitai LoRAs over by URL, as it does for wan-2-2-gv.
import hashlib
import os
import urllib.request

import runpod

import comfy_handler

LORAS = '/comfyui/models/loras'


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
    return comfy_handler.handler(job)


if __name__ == '__main__':
    runpod.serverless.start({'handler': handler})
