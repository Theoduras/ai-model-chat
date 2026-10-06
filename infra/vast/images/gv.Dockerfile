# wan-2-2-gv for Vast: the generate_video build RunPod ran (a9247705c), baked,
# so a new worker pulls it instead of building on boot.
FROM wlsdml1114/engui_genai-base_blackwell:1.1
COPY gv_steps.py /tmp/
RUN git clone https://github.com/wlsdml1114/generate_video /src && cd /src && git checkout a9247705c \
 && python3 /tmp/gv_steps.py > /tmp/b.sh && bash /tmp/b.sh \
 && cp -rn /src/. / && cp /src/extra_model_paths.yaml /ComfyUI/ && chmod +x /entrypoint.sh \
 && touch /gv_built && rm -rf /src /tmp/b.sh /tmp/gv_steps.py /root/.cache
