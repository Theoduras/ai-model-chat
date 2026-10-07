# Boot patch for our RunPod generate_video endpoint (RUNPOD_GV_ENDPOINT).
#
# The hub image (wlsdml1114/generate_video, build a9247705c) runs fixed ComfyUI
# workflows: 8 steps, one hard-coded noise seed, and LoRAs only by filename on
# its own disk. This makes it take what imagegen sends instead -- `seed`,
# `steps`, and LoRA *URLs*, downloaded once per worker -- so it runs the same
# library as the public wan-2-2-lora endpoint with no image of our own.
#
# Installed as the template's start command, base64 so no quoting survives:
#   bash -c 'echo <base64 of this file> | base64 -d > /gv_patch.py && python /gv_patch.py; exec /entrypoint.sh'
# A patch that no longer applies leaves the handler untouched rather than broken.

HANDLER = '/handler.py'
MARK = '# gv-patch'

ADDED = MARK + '''
import hashlib as _gv_hash

_GV_LORAS = '/ComfyUI/models/loras'
_GV_JOB = {}


def _gv_fetch(url):
    if not isinstance(url, str) or not url.startswith('http'):
        return url
    # Named without the query, so a rotated download token reuses the file.
    name = 'gv_' + _gv_hash.sha1(url.split('?')[0].encode()).hexdigest()[:16] + '.safetensors'
    path = os.path.join(_GV_LORAS, name)
    if not os.path.exists(path):
        logger.info(f'LoRA download: {url.split("?")[0]} -> {name}')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=900) as r, open(path + '.part', 'wb') as f:
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(path + '.part', path)
    return name


_gv_load = load_workflow


def load_workflow(workflow_path):
    prompt = _gv_load(workflow_path)
    if _GV_JOB.get('seed') is not None and '835' in prompt:
        prompt['835']['inputs']['noise_seed'] = int(_GV_JOB['seed'])
    if _GV_JOB.get('steps') and '834' in prompt and '829' in prompt:
        steps = max(2, int(_GV_JOB['steps']))
        prompt['834']['inputs']['steps'] = steps
        # High-noise model for the first half, low-noise for the rest.
        prompt['829']['inputs']['step'] = steps // 2
    # torch.compile breaks on the newer ComfyUI's fp8 requantize: route each
    # compile node's consumers straight to the model it was handed.
    for cid, node in list(prompt.items()):
        if 'Compile' in node.get('class_type', '') and isinstance(node['inputs'].get('model'), list):
            src = node['inputs']['model']
            for other in prompt.values():
                for k, v in other.get('inputs', {}).items():
                    if isinstance(v, list) and len(v) == 2 and str(v[0]) == cid:
                        other['inputs'][k] = src
            del prompt[cid]
    # Newer ComfyUI-Frame-Interpolation (the Vast image) made these required.
    for node in prompt.values():
        if node.get('class_type') == 'RIFE VFI':
            for k, v in (('dtype', 'float32'), ('torch_compile', False), ('batch_size', 1)):
                node['inputs'].setdefault(k, v)
    return prompt


_gv_queue = queue_prompt


def queue_prompt(prompt):
    # Newer ComfyUI (the Vast image) only loads images from its input folder.
    import shutil
    for node in prompt.values():
        img = node.get('inputs', {}).get('image') if node.get('class_type') == 'LoadImage' else None
        if isinstance(img, str) and img.startswith('/') and os.path.exists(img):
            name = img.strip('/').replace('/', '_')
            os.makedirs('/ComfyUI/input', exist_ok=True)
            shutil.copy(img, os.path.join('/ComfyUI/input', name))
            node['inputs']['image'] = name
    return _gv_queue(prompt)


_gv_handler = handler


def handler(job):
    # Before the stock handler logs its input: the URLs carry a download token.
    job_input = job.get('input') or {}
    for pair in job_input.get('lora_pairs') or []:
        for side in ('high', 'low'):
            if pair.get(side):
                pair[side] = _gv_fetch(pair[side])
    _GV_JOB.clear()
    _GV_JOB.update(job_input)
    return _gv_handler(job)


'''


def patch(src):
    start = 'runpod.serverless.start('
    if start not in src:
        return src
    if MARK in src:
        # A restarted worker keeps its disk: swap in this version of the block.
        src = src[:src.index(MARK)] + src[src.rindex(start):]
    needed = ('def load_workflow(', 'def handler(', 'def queue_prompt(')
    if not all(n in src for n in needed):
        return src
    at = src.rindex(start)
    return src[:at] + ADDED + src[at:]


if __name__ == '__main__':
    try:
        with open(HANDLER) as f:
            src = f.read()
        out = patch(src)
        if out != src:
            compile(out, HANDLER, 'exec')
            with open(HANDLER, 'w') as f:
                f.write(out)
            print('gv-patch applied')
        else:
            print('gv-patch skipped')
    except Exception as e:
        print(f'gv-patch failed, handler left as is: {e}')
