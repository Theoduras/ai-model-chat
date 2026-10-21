# Wan 2.2 Animate (replace mode) for RUNPOD_ANIMATE_ENDPOINT: her still takes
# the place of the person in an uploaded clip, motion and scene kept.
# Weights (~75 GB with the preprocess checkpoints) live on the network volume,
# fetched by handler.py on a worker's first start, so the image stays small.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git /wan \
 && pip install --no-cache-dir -r /wan/requirements.txt -r /wan/requirements_animate.txt \
 && pip install --no-cache-dir runpod requests "huggingface_hub[cli]"
# flash-attn is optional for Wan; a failed wheel build must not fail the image.
RUN pip install --no-cache-dir flash-attn --no-build-isolation || true

COPY handler.py /handler.py
WORKDIR /wan
CMD ["python3", "-u", "/handler.py"]
