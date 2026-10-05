# MiniMax H3 worker (`h3-gv`)

Explicit Photo to Video with its own sound, on our RunPod serverless.
The app sends the whole ComfyUI workflow (`imagegen.h3_payload`), so the worker
is stock `worker-comfyui` plus the H3 weights and a LoRA-link fetcher.

## Deploy

```
RUNPOD_API_KEY=... python infra/runpod-h3/deploy.py [datacenter]   # default EU-RO-1
RUNPOD_API_KEY=... python infra/runpod-h3/deploy.py test <endpoint id>
```

`deploy.py` creates, by name, and reuses on a re-run:

1. `h3-models`, an 80 GB network volume (about $5.60 a month), which pins the
   endpoint to that datacenter.
2. `h3-download`, a CPU pod that downloads the ~52 GB of weights onto the volume,
   resuming cut files, and is deleted when it is done or fails.
3. `h3-worker`, a template on stock `worker-comfyui` whose start command installs
   `handler.py`. A change to `handler.py` needs a re-run to reach the workers.
4. `h3-gv`, the endpoint: 80 GB GPU (H100/A100), 0 active, max 2, FlashBoot,
   5 min idle, 30 min job timeout. It prints the endpoint id.

`test` sends one 5 s 480p clip through `imagegen.RunPodProvider`, the path the
studio uses, saves `h3-test.mp4` and prints how long it queued and ran.

Then on Cloud Run: `--update-env-vars RUNPOD_H3_ENDPOINT=<endpoint id>`, and correct
`h3-gv` in `credits.PROVIDER_COST_USD` from the timed clip.

## Baked image instead

`Dockerfile` bakes the weights into the image (~55 GB) for a RunPod GitHub build,
which works in any datacenter but is set up in the console only, rebuilds on a
GitHub release rather than a push, and must finish `docker build` in 30 minutes.
