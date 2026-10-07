# Vast start for h3-gv: our handler over worker-comfyui, weights fetched first.
mv -n /handler.py /comfy_handler.py
cp /opt/vast_jobs/runpod-h3/handler.py /handler.py
# The vast-h3 image carries each weight as 2 GB parts (h3_split.ps1); join them once.
for first in $(find /h3parts -name '*.part000' 2>/dev/null); do
  rel=${first#/h3parts/}; rel=${rel%.part000}; dest=/comfyui/models/$rel
  [ -f "$dest" ] || { mkdir -p "$(dirname "$dest")"; cat "/h3parts/$rel".part* > "$dest.tmp" && mv "$dest.tmp" "$dest"; }
done
# The parts carry HF's folders; the worker reads unet/clip/vae (H3_FILES).
# The link back keeps the join above from redoing it on a restart.
python3 -c "
import json, os
for folder, rel in json.loads(os.environ['H3_FILES']):
    src, dst = '/comfyui/models/' + rel, '/comfyui/models/%s/%s' % (folder, rel.split('/')[-1])
    if os.path.exists(src) and not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True); os.rename(src, dst); os.symlink(dst, src)
"
RUNPOD_API_KEY=- python3 -u -c "import sys; sys.path.insert(0, '/opt/vast_jobs/runpod-h3'); import deploy; exec(deploy.GET)"
python3 /opt/vast_jobs/preload.py h3 /comfyui/models/loras h3_ || true
exec /start.sh
