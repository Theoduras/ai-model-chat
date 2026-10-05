# MiniMax H3 worker (`h3-gv`)

Explicit Photo to Video with its own sound, on our RunPod serverless.
The app sends the whole ComfyUI workflow (`imagegen.h3_payload`), so this image
is stock `worker-comfyui` plus the H3 weights and a LoRA-link fetcher.

1. RunPod console → Serverless → New Endpoint → GitHub repo:
   `theoduras/ai-model-chat`, branch `develop`, Dockerfile path
   `infra/runpod-h3/Dockerfile`, build context `infra/runpod-h3`. RunPod builds
   the image (~55 GB, weights baked in) and rebuilds on every push to `develop`.
2. Serverless endpoint: 80 GB GPU (H100/A100; 48 GB is untested), 0 active, max 2,
   FlashBoot, 5 min idle, container disk 80 GB, env `CIVITAI_TOKEN` not needed
   (the app puts the token on the link).
3. On Cloud Run: `--update-env-vars RUNPOD_H3_ENDPOINT=<endpoint id>`.
4. Time one 5s 720p clip and correct `h3-gv` in `credits.PROVIDER_COST_USD`.
