# syntax=docker/dockerfile:1.7
# h3-gv for Vast: infra/runpod-h3/Dockerfile's weights, baked, one --link layer
# each; start/h3-gv.sh still installs the handler, and its weight fetch finds
# every file present.
FROM runpod/worker-comfyui:5.10.0-base
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_pruned_w6a8.safetensors /comfyui/models/diffusion_models/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors /comfyui/models/text_encoders/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors /comfyui/models/vae/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors /comfyui/models/vae/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors /comfyui/models/loras/
