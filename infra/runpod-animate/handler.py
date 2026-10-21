"""RunPod worker: Wan 2.2 Animate in replace mode, no safety checker.

Input:  {image_url, video_url, prompt, seed, steps, resolution: '480p'|'720p'}
        (either url may be a data: URI)
Output: {video: <base64 mp4>}, the clip's own audio copied back.
"""
import base64
import os
import random
import shutil
import subprocess
import tempfile

import requests

import sys

CKPT = '/runpod-volume/animate/Wan2.2-Animate-14B'
AREA = {'480p': (832, 480), '720p': (1280, 720)}


def ensure_weights():
    if not os.path.exists(os.path.join(CKPT, '.complete')):
        try:
            import hf_transfer  # noqa: F401
        except ImportError:
            # The base image turns fast download on without installing it.
            os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '0'
        from huggingface_hub import snapshot_download
        # The CLIP .pth already holds xlm-roberta's weights; only its tokenizer is read.
        snapshot_download('Wan-AI/Wan2.2-Animate-14B', local_dir=CKPT, max_workers=16,
                          ignore_patterns=['assets/*', 'xlm-roberta-large/onnx/*',
                                           'xlm-roberta-large/*.bin', 'xlm-roberta-large/*.h5',
                                           'xlm-roberta-large/*.msgpack',
                                           'xlm-roberta-large/*.safetensors'])
        open(os.path.join(CKPT, '.complete'), 'w').close()


def fetch(src, path):
    if src.startswith('data:'):
        data = base64.b64decode(src.split(',', 1)[1])
    else:
        r = requests.get(src, timeout=300)
        r.raise_for_status()
        data = r.content
    with open(path, 'wb') as f:
        f.write(data)


_MODELS = None


def models():
    # Loaded once per worker and kept on the GPU; reloading ~60 GB from the network
    # volume was most of every job's time.
    global _MODELS
    if _MODELS is None:
        root = '/runpod-volume/animate/Wan2.2'
        sys.path[:0] = [root, os.path.join(root, 'wan/modules/animate/preprocess')]
        import wan
        from wan.configs import WAN_CONFIGS
        from process_pipepline import ProcessPipeline
        ck = f'{CKPT}/process_checkpoint'
        prep = ProcessPipeline(det_checkpoint_path=f'{ck}/det/yolov10m.onnx',
                               pose2d_checkpoint_path=f'{ck}/pose2d/vitpose_h_wholebody.onnx',
                               sam_checkpoint_path=f'{ck}/sam2/sam2_hiera_large.pt',
                               flux_kontext_path=None)
        cfg = WAN_CONFIGS['animate-14B']
        model = wan.WanAnimate(config=cfg, checkpoint_dir=CKPT, device_id=0, rank=0,
                               t5_fsdp=False, dit_fsdp=False, use_sp=False, t5_cpu=False,
                               convert_model_dtype=True, use_relighting_lora=True)
        _MODELS = prep, model, cfg
    return _MODELS


def handler(job):
    inp = job['input']
    if not inp.get('image_url') or not inp.get('video_url'):
        return {'error': 'image_url and video_url are required'}
    ensure_weights()
    work = tempfile.mkdtemp()
    try:
        ref, clip = os.path.join(work, 'ref.png'), os.path.join(work, 'clip.mp4')
        fetch(inp['image_url'], ref)
        fetch(inp['video_url'], clip)
        pre, out = os.path.join(work, 'pre'), os.path.join(work, 'out.mp4')
        os.makedirs(pre)
        w, h = AREA.get(inp.get('resolution') or '720p', AREA['720p'])
        prep, model, cfg = models()
        prep(video_path=clip, refer_image_path=ref, output_path=pre, resolution_area=[w, h],
             iterations=3, k=7, w_len=1, h_len=1, replace_flag=True)
        seed = inp.get('seed')
        video = model.generate(src_root_path=pre, replace_flag=True, refert_num=1,
                               clip_len=cfg.frame_num, shift=cfg.sample_shift, sample_solver='unipc',
                               sampling_steps=int(inp.get('steps') or 20),
                               guide_scale=cfg.sample_guide_scale,
                               seed=int(seed) if seed is not None else random.randint(0, 2**31 - 1),
                               offload_model=False)
        from wan.utils.utils import save_video
        save_video(tensor=video[None], save_file=out, fps=cfg.sample_fps, nrow=1,
                   normalize=True, value_range=(-1, 1))
        del video
        import torch
        torch.cuda.empty_cache()
        final = os.path.join(work, 'final.mp4')
        # The clip's sound, if it has any; a silent clip just keeps the render.
        mux = subprocess.run(['ffmpeg', '-y', '-i', out, '-i', clip, '-map', '0:v', '-map', '1:a?',
                              '-c:v', 'copy', '-c:a', 'aac', '-shortest', final])
        with open(final if mux.returncode == 0 else out, 'rb') as f:
            return {'video': base64.b64encode(f.read()).decode()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__' and sys.argv[1:] == ['--serve']:
    # Started by the bootstrap in deploy.py once the venv exists: the models stay loaded.
    import runpod
    runpod.serverless.start({'handler': handler})
elif __name__ == '__main__':
    # The first install's job: one job's input file in, its output file out.
    import json
    with open(sys.argv[1]) as f:
        result = handler({'input': json.load(f)})
    with open(sys.argv[2], 'w') as f:
        json.dump(result, f)
