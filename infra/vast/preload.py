# Downloads every enabled video LoRA of one family at worker boot, under the
# name the handler's own fetcher would give it, so no clip waits on one:
#   python3 preload.py wan|h3 <loras dir> <prefix>
# Needs VJ_APP_URL and VJ_LORA_KEY (the app's VAST_LORA_KEY); without them it skips.
import hashlib
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

family, folder, prefix = sys.argv[1:4]
app, key = os.getenv('VJ_APP_URL', '').rstrip('/'), os.getenv('VJ_LORA_KEY', '')
if not app or not key:
    sys.exit(print('lora preload: VJ_APP_URL or VJ_LORA_KEY unset, skipped', flush=True))
req = urllib.request.Request(f'{app}/api/generate/lora-links',
                             headers={'Authorization': f'Bearer {key}'})
with urllib.request.urlopen(req, timeout=60) as r:
    urls = sorted(set(json.loads(r.read()).get(family) or ()))
os.makedirs(folder, exist_ok=True)


def get(url):
    path = os.path.join(folder, prefix + hashlib.sha1(url.split('?')[0].encode()).hexdigest()[:16]
                        + '.safetensors')
    if os.path.exists(path):
        return
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=900) as r, open(path + '.part', 'wb') as f:
            while chunk := r.read(1 << 22):
                f.write(chunk)
        os.replace(path + '.part', path)
    except Exception as e:
        print('lora preload failed:', url.split('?')[0], repr(e), flush=True)


with ThreadPoolExecutor(4) as pool:
    list(pool.map(get, urls))
print(f'lora preload: {len(urls)} {family} files ready', flush=True)
