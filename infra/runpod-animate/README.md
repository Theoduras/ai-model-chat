# Wan 2.2 Animate worker (`wan-2-2-animate-gv`)

Explicit Swap's default once `RUNPOD_ANIMATE_ENDPOINT` is set. The studio first makes
a "Swap her in" still on the clip's first frame from her character, then this worker
replaces the person in the clip with that still (Wan 2.2 Animate, replace mode,
relighting LoRA), and the clip's sound is copied back.

`RUNPOD_API_KEY=... python infra/runpod-animate/deploy.py` creates or updates endpoint
`qfdnou9hy8d3so` (the code default). No image of ours: a stock PyTorch image installs Wan
2.2 into a venv on the volume on first start.

The first job downloads the weights onto the volume (~61 GB, 16 parallel streams, once). The model
stays on an 80 GB+ GPU (no CPU offload) and pose detection runs on onnxruntime-gpu. Price in
`credits.py` is a guess (2x `wan-2-2-gv`) until a real run is timed. Not yet run.
