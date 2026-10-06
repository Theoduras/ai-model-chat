# Vast start for wan-2-2-gv. The vast-gv image comes built (/gv_built); on any
# other image a worker builds it from the author's repo on first boot.
set -e
if [ ! -f /gv_built ]; then
  rm -rf /src && git clone https://github.com/wlsdml1114/generate_video /src
  cd /src && git checkout a9247705c || true
  curl -fsSL https://raw.githubusercontent.com/Theoduras/ai-model-chat/$VJ_REF/infra/vast/images/gv_steps.py | python3 - > /tmp/gv_build.sh
  bash /tmp/gv_build.sh
  cp -rn /src/. / && cp /src/extra_model_paths.yaml /ComfyUI/ && chmod +x /entrypoint.sh
  touch /gv_built
fi
python3 /opt/vast_jobs/preload.py wan /ComfyUI/models/loras gv_ || true
cd / && python3 /opt/vast_jobs/runpod-gv-patch.py
exec /entrypoint.sh
