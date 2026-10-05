# MiniMax H3 image-to-video with its own audio, for RUNPOD_H3_ENDPOINT.
# The weights are baked in (~35 GB) so a cold worker never downloads them.
# nvfp4 and w6a8 are the smallest files and need a Blackwell GPU (RTX PRO 6000).
FROM runpod/worker-comfyui:5.10.0-base

ARG HF=https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main
RUN cd /comfyui/models \
 && comfy_get() { mkdir -p "$1" && wget -q --show-progress -O "$1/$(basename "$2")" "$HF/$2"; } \
 && comfy_get diffusion_models diffusion_models/minimax_h3_fl2va_pruned_w6a8.safetensors \
 && comfy_get text_encoders text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
 && comfy_get vae vae/minimax_h3_video_vae_int8_convrot.safetensors \
 && comfy_get vae vae/minimax_h3_audio_vae_fp32.safetensors \
 && comfy_get loras loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors

# Stock handler kept as a module; ours fetches LoRA links, then hands over.
RUN mv /handler.py /comfy_handler.py
COPY handler.py /handler.py
