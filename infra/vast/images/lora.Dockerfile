# wan-2-2-char for Vast: ai-toolkit with the Wan 2.2 base the LoRA handler
# trains and draws on, baked into HF_HOME. MiniMax H3 (opt-in) still downloads
# on its first job: baking it too would push the image past 150 GB.
FROM ostris/aitoolkit:latest
ENV HF_HOME=/workspace/hf MODELS_PATH=/workspace/models HF_HUB_ENABLE_HF_TRANSFER=1
RUN pip install -q --break-system-packages --ignore-installed cryptography runpod "huggingface_hub[hf_transfer]" \
 && python3 -c "from huggingface_hub import snapshot_download as s; \
s('ai-toolkit/Wan2.2-T2V-A14B-Diffusers-bf16'); \
s('Wan-AI/Wan2.2-T2V-A14B-Diffusers', ignore_patterns=['transformer/*', 'transformer_2/*'])"
