# Vast start for h3-gv: our handler over worker-comfyui, weights fetched first.
mv -n /handler.py /comfy_handler.py
cp /opt/vast_jobs/runpod-h3/handler.py /handler.py
# The vast-h3 image carries each weight as 2 GB parts (h3_split.ps1); join them once.
for first in $(find /h3parts -name '*.part000' 2>/dev/null); do
  rel=${first#/h3parts/}; rel=${rel%.part000}; dest=/comfyui/models/$rel
  [ -f "$dest" ] || { mkdir -p "$(dirname "$dest")"; cat "/h3parts/$rel".part* > "$dest.tmp" && mv "$dest.tmp" "$dest"; }
done
RUNPOD_API_KEY=- python3 -u -c "import sys; sys.path.insert(0, '/opt/vast_jobs/runpod-h3'); import deploy; exec(deploy.GET)"
python3 /opt/vast_jobs/preload.py h3 /comfyui/models/loras h3_ || true
exec /start.sh
