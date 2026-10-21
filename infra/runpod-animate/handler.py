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
import runpod

import sys

CKPT = '/runpod-volume/animate/Wan2.2-Animate-14B'
AREA = {'480p': (832, 480), '720p': (1280, 720)}


def ensure_weights():
    if not os.path.exists(os.path.join(CKPT, '.complete')):
        from huggingface_hub import snapshot_download
        snapshot_download('Wan-AI/Wan2.2-Animate-14B', local_dir=CKPT)
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
        w, h = AREA.get(inp.get('resolution') or '720p', AREA['720p'])
        subprocess.run([sys.executable, 'wan/modules/animate/preprocess/preprocess_data.py',
                        '--ckpt_path', f'{CKPT}/process_checkpoint',
                        '--video_path', clip, '--refer_path', ref, '--save_path', pre,
                        '--resolution_area', str(w), str(h),
                        '--iterations', '3', '--k', '7', '--w_len', '1', '--h_len', '1',
                        '--replace_flag'], check=True)
        seed = inp.get('seed')
        subprocess.run([sys.executable, 'generate.py', '--task', 'animate-14B', '--ckpt_dir', CKPT,
                        '--src_root_path', pre, '--refert_num', '1', '--replace_flag',
                        '--use_relighting_lora', '--offload_model', 'True',
                        '--convert_model_dtype',
                        '--sample_steps', str(int(inp.get('steps') or 20)),
                        '--base_seed', str(int(seed) if seed is not None else random.randint(0, 2**31 - 1)),
                        '--prompt', inp.get('prompt') or 'a woman moving naturally',
                        '--save_file', out], check=True)
        final = os.path.join(work, 'final.mp4')
        # The clip's sound, if it has any; a silent clip just keeps the render.
        mux = subprocess.run(['ffmpeg', '-y', '-i', out, '-i', clip, '-map', '0:v', '-map', '1:a?',
                              '-c:v', 'copy', '-c:a', 'aac', '-shortest', final])
        with open(final if mux.returncode == 0 else out, 'rb') as f:
            return {'video': base64.b64encode(f.read()).decode()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


runpod.serverless.start({'handler': handler})
