# syntax=docker/dockerfile:1.7
# wan-2-2-gv for Vast: the generate_video build RunPod ran (a9247705c), baked,
# so a new worker pulls it instead of building on boot. Each weight file is its
# own --link layer, so they download, push and pull in parallel.
FROM wlsdml1114/engui_genai-base_blackwell:1.1
COPY gv_steps.py /tmp/
RUN git clone https://github.com/wlsdml1114/generate_video /src && cd /src && git checkout a9247705c \
 && python3 /tmp/gv_steps.py --no-weights > /tmp/b.sh && bash /tmp/b.sh \
 && cp -rn /src/. / && cp /src/extra_model_paths.yaml /ComfyUI/ && chmod +x /entrypoint.sh \
 && touch /gv_built && rm -rf /src /tmp/b.sh /tmp/gv_steps.py /root/.cache
ADD --link https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors /ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
ADD --link https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors /ComfyUI/models/vae/wan_2.1_vae.safetensors
ADD --link https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors /ComfyUI/models/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
ADD --link https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors /ComfyUI/models/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors
ADD --link https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors /ComfyUI/models/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors
ADD --link https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors /ComfyUI/models/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
