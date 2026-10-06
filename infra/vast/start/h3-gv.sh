# Vast start for h3-gv: our handler over worker-comfyui, weights fetched first.
mv -n /handler.py /comfy_handler.py
cp /opt/vast_jobs/runpod-h3/handler.py /handler.py
RUNPOD_API_KEY=- python3 -u -c "import sys; sys.path.insert(0, '/opt/vast_jobs/runpod-h3'); import deploy; exec(deploy.GET)"
exec /start.sh
