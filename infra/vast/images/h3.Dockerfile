# syntax=docker/dockerfile:1.7
# h3-gv for Vast: worker-comfyui with the int8 H3 weights baked in, one --link
# layer each, so a new worker pulls them instead of downloading ~50 GB from
# Hugging Face; start/h3-gv.sh's fetch then finds every file present.
FROM runpod/worker-comfyui:5.10.0-base
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors /comfyui/models/diffusion_models/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors /comfyui/models/text_encoders/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors /comfyui/models/vae/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors /comfyui/models/vae/
ADD --link https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors /comfyui/models/loras/
