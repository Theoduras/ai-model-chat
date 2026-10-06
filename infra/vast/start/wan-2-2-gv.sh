# Vast start for wan-2-2-gv. The image has no public build, so a worker
# builds it from the author's repo on first boot (pinned to the build RunPod
# ran) and keeps it on its disk.
set -e
if [ ! -f /gv_built ]; then
  rm -rf /src && git clone https://github.com/wlsdml1114/generate_video /src
  cd /src && git checkout a9247705c || true
  python3 - <<'PY' > /tmp/gv_build.sh
import re
text = re.sub(r'\\\n', ' ', open('/src/Dockerfile').read())
print('set -e; cd /')
for line in text.splitlines():
    if line.startswith('RUN '):
        print(line[4:].replace('wget -q', 'wget -q -c'))
PY
  bash /tmp/gv_build.sh
  cp -rn /src/. / && cp /src/extra_model_paths.yaml /ComfyUI/ && chmod +x /entrypoint.sh
  touch /gv_built
fi
python3 /opt/vast_jobs/preload.py wan /ComfyUI/models/loras gv_ || true
cd / && python3 /opt/vast_jobs/runpod-gv-patch.py
exec /entrypoint.sh
