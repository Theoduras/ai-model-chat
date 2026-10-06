# One worker for a character's own LoRA (`char-lora` endpoint), on the stock
# ostris/aitoolkit image:
#   {"train": {...}}  trains a Wan 2.2 T2V A14B LoRA on her approved photos with
#                     ai-toolkit and PUTs the high/low noise files to the signed
#                     URLs the app hands over (too big for a job's output).
#                     With `h3_put` it then trains a MiniMax H3 LoRA on the
#                     same photos, for the H3 clips (`h3-gv`).
#   {"image": {...}}  one still from the same base model with her LoRA, so the
#                     photos and the explicit clips share one identity file.
# Both read the base weights from the network volume (HF_HOME), downloaded on
# the first job and kept.
import base64
import gc
import glob
import hashlib
import io
import os
import random
import subprocess
import urllib.request

import runpod

BASE = os.getenv('LORA_BASE_MODEL', 'ai-toolkit/Wan2.2-T2V-A14B-Diffusers-bf16')
H3_BASE = os.getenv('LORA_H3_BASE_MODEL', 'Comfy-Org/MiniMax-H3')
H3_ADAPTER = 'ostris/minimax_h3_training_adapter/minimax_h3_training_adapter_v3.safetensors'
PIPE_REPO = os.getenv('LORA_PIPE_REPO', 'Wan-AI/Wan2.2-T2V-A14B-Diffusers')
TOOLKIT = '/app/ai-toolkit'
WORK = '/tmp/char-lora'
CACHE = os.path.join(os.getenv('HF_HOME', '/runpod-volume/hf'), 'char-loras')
UA = {'User-Agent': 'Mozilla/5.0'}
_pipe = {}


def get(url, path):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=900) as r, \
            open(path + '.part', 'wb') as f:
        while chunk := r.read(1 << 22):
            f.write(chunk)
    os.replace(path + '.part', path)


def put(url, path):
    req = urllib.request.Request(url, data=open(path, 'rb'), method='PUT', headers={
        'Content-Type': 'application/octet-stream',
        'Content-Length': str(os.path.getsize(path))})
    with urllib.request.urlopen(req, timeout=900) as r:
        r.read()


def drop_pipe():
    if _pipe:
        _pipe.clear()
        gc.collect()
        import torch
        torch.cuda.empty_cache()


def config(name, data, steps, rank):
    # Text embeddings are cached, so the trigger rides in every caption rather
    # than as ai-toolkit's trigger_word, which caching would ignore.
    import yaml
    return yaml.safe_dump({'job': 'extension', 'config': {'name': name, 'process': [{
        'type': 'sd_trainer', 'training_folder': WORK + '/out', 'device': 'cuda:0',
        'network': {'type': 'lora', 'linear': rank, 'linear_alpha': rank},
        'save': {'dtype': 'float16', 'save_every': steps, 'max_step_saves_to_keep': 1},
        'datasets': [{'folder_path': data, 'caption_ext': 'txt', 'caption_dropout_rate': 0.05,
                      'num_frames': 1, 'resolution': [512, 768, 1024]}],
        'train': {'batch_size': 1, 'steps': steps, 'gradient_accumulation': 1,
                  'train_unet': True, 'train_text_encoder': False,
                  'gradient_checkpointing': True, 'noise_scheduler': 'flowmatch',
                  'timestep_type': 'linear', 'optimizer': 'adamw8bit', 'lr': 1e-4,
                  'optimizer_params': {'weight_decay': 1e-4}, 'dtype': 'bf16',
                  'switch_boundary_every': 10, 'cache_text_embeddings': True,
                  'disable_sampling': True, 'skip_first_sample': True},
        'model': {'name_or_path': BASE, 'arch': 'wan22_14b', 'quantize': True,
                  'qtype': 'qfloat8', 'quantize_te': True, 'qtype_te': 'qfloat8',
                  'low_vram': True,
                  'model_kwargs': {'train_high_noise': True, 'train_low_noise': True}},
    }]}})


def config_h3(name, data, steps, rank):
    # ai-toolkit's own MiniMax-H3 defaults: the Comfy weights are pre-quantized
    # (convrot8 DiT, nvfp4 TE), and the guidance-distilled model trains through
    # Ostris's assistant adapter.
    import yaml
    return yaml.safe_dump({'job': 'extension', 'config': {'name': name, 'process': [{
        'type': 'sd_trainer', 'training_folder': WORK + '/out', 'device': 'cuda:0',
        'network': {'type': 'lora', 'linear': rank, 'linear_alpha': rank,
                    'network_kwargs': {'ignore_if_contains': ['adaln_proj']}},
        'save': {'dtype': 'bf16', 'save_every': steps, 'max_step_saves_to_keep': 1},
        'datasets': [{'folder_path': data, 'caption_ext': 'txt', 'caption_dropout_rate': 0.05,
                      'num_frames': 1, 'resolution': [512, 768], 'cache_latents_to_disk': True}],
        'train': {'batch_size': 1, 'steps': steps, 'gradient_accumulation': 1,
                  'train_unet': True, 'train_text_encoder': False,
                  'gradient_checkpointing': True, 'noise_scheduler': 'flowmatch',
                  'timestep_type': 'shift', 'optimizer': 'adamw8bit', 'lr': 1e-4,
                  'optimizer_params': {'weight_decay': 1e-4}, 'dtype': 'bf16',
                  'cache_text_embeddings': True, 'disable_sampling': True,
                  'skip_first_sample': True},
        'model': {'name_or_path': H3_BASE, 'arch': 'minimax_h3', 'quantize': True,
                  'qtype': 'convrot8', 'quantize_te': True, 'qtype_te': 'nvfp4',
                  'low_vram': True, 'assistant_lora_path': H3_ADAPTER},
    }]}})


def train_h3(job, args, data, name, steps):
    name += '_h3'
    cfg = f'{WORK}/{name}.yaml'
    with open(cfg, 'w') as f:
        f.write(config_h3(name, data, steps, 16))
    runpod.serverless.progress_update(job, f'training H3 {steps} steps')
    run = subprocess.run(['python', 'run.py', cfg], cwd=TOOLKIT, capture_output=True, text=True)
    files = sorted(glob.glob(f'{WORK}/out/{name}/*.safetensors'), key=os.path.getmtime)
    if run.returncode or not files:
        return {'error': f'H3 training failed ({run.returncode}): ' + (run.stderr or run.stdout)[-1500:]}
    put(args['h3_put'], files[-1])
    return {'bytes': os.path.getsize(files[-1])}


def train(job, args):
    drop_pipe()
    name = 'char_' + hashlib.sha1(str(args['high_put']).split('?')[0].encode()).hexdigest()[:12]
    data = f'{WORK}/{name}/data'
    os.makedirs(data, exist_ok=True)
    for i, item in enumerate(args['images']):
        get(item['url'], f'{data}/{i:03d}.jpg')
        with open(f'{data}/{i:03d}.txt', 'w') as f:
            f.write(item.get('caption') or args['trigger'])
    steps = int(args.get('steps') or 1500)
    cfg = f'{WORK}/{name}/config.yaml'
    with open(cfg, 'w') as f:
        f.write(config(name, data, steps, int(args.get('rank') or 32)))
    runpod.serverless.progress_update(job, f'training {steps} steps')
    run = subprocess.run(['python', 'run.py', cfg], cwd=TOOLKIT, capture_output=True, text=True)
    out = f'{WORK}/out/{name}'
    high = sorted(glob.glob(f'{out}/*_high_noise.safetensors'), key=os.path.getmtime)
    low = sorted(glob.glob(f'{out}/*_low_noise.safetensors'), key=os.path.getmtime)
    if run.returncode or not (high and low):
        return {'error': f'training failed ({run.returncode}): ' + (run.stderr or run.stdout)[-1500:]}
    put(args['high_put'], high[-1])
    put(args['low_put'], low[-1])
    size = os.path.getsize(high[-1]) + os.path.getsize(low[-1])
    if args.get('h3_put'):
        h3 = train_h3(job, args, data, name, steps)
        if h3.get('error'):
            return h3
        size += h3['bytes']
    return {'trained': True, 'steps': steps, 'bytes': size}


def lora_file(url):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(url.split('?')[0].encode()).hexdigest()[:16] + '.safetensors')
    if not os.path.exists(path):
        get(url, path)
    return path


def pipeline():
    if 'pipe' not in _pipe:
        import torch
        from diffusers import WanPipeline, WanTransformer3DModel
        # BASE holds only the two bf16 experts; VAE, text encoder and scheduler
        # come from the official repo, skipping its fp32 experts.
        experts = {k: WanTransformer3DModel.from_pretrained(BASE, subfolder=k, torch_dtype=torch.bfloat16)
                   for k in ('transformer', 'transformer_2')}
        _pipe['pipe'] = WanPipeline.from_pretrained(PIPE_REPO, torch_dtype=torch.bfloat16,
                                                    **experts).to('cuda')
        _pipe['loras'] = None
    return _pipe['pipe']


def image(job, args):
    import torch
    pipe = pipeline()
    loras = [l for l in args.get('loras') or [] if l.get('high') and l.get('low')][:3]
    key = tuple((l['high'].split('?')[0], float(l.get('scale', 1))) for l in loras)
    if _pipe['loras'] != key:
        pipe.unload_lora_weights()
        names = []
        for i, l in enumerate(loras):
            pipe.load_lora_weights(lora_file(l['high']), adapter_name=f'h{i}')
            pipe.load_lora_weights(lora_file(l['low']), adapter_name=f'l{i}',
                                   load_into_transformer_2=True)
            names += [f'h{i}', f'l{i}']
        if names:
            scales = [float(l.get('scale', 1)) for l in loras for _ in (0, 1)]
            pipe.set_adapters(names, adapter_weights=scales)
        _pipe['loras'] = key
    seed = args.get('seed')
    seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
    w, h = int(args.get('width') or 1024), int(args.get('height') or 1536)
    out = pipe(prompt=args['prompt'], negative_prompt=args.get('negative') or '',
               width=w - w % 16, height=h - h % 16, num_frames=1,
               num_inference_steps=int(args.get('steps') or 30),
               guidance_scale=float(args.get('guidance') or 3.5),
               generator=torch.Generator('cuda').manual_seed(seed), output_type='pil')
    frame = out.frames[0][0]
    buf = io.BytesIO()
    frame.save(buf, format='PNG')
    return {'image': base64.b64encode(buf.getvalue()).decode(), 'seed': seed}


def handler(job):
    args = job.get('input') or {}
    if args.get('train'):
        return train(job, args['train'])
    if args.get('image'):
        return image(job, args['image'])
    return {'error': 'nothing to do: send "train" or "image"'}


if __name__ == '__main__':
    runpod.serverless.start({'handler': handler})
