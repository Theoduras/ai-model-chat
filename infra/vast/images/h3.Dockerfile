# h3-gv for Vast: infra/runpod-h3/Dockerfile's weights, baked; start/h3-gv.sh
# still installs the handler, and its weight fetch finds every file present.
FROM runpod/worker-comfyui:5.10.0-base
ARG HF=https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main
RUN cd /comfyui/models \
 && get() { mkdir -p "$1" && wget -q -O "$1/$(basename "$2")" "$HF/$2"; } \
 && get diffusion_models diffusion_models/minimax_h3_fl2va_pruned_w6a8.safetensors \
 && get text_encoders text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
 && get vae vae/minimax_h3_video_vae_int8_convrot.safetensors \
 && get vae vae/minimax_h3_audio_vae_fp32.safetensors \
 && get loras loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors
