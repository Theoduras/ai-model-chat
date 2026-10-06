#!/bin/bash
# Runs on a Vast VM instance (Docker works there): builds the three worker
# images and pushes them to ghcr.io/theoduras. GHCR_TOKEN comes from the Vast
# account's environment variables. Writes /root/build.status as it goes.
set -euo pipefail
say() { echo "$*" | tee -a /root/build.status; }
command -v docker >/dev/null || (curl -fsSL https://get.docker.com | sh)
echo "$GHCR_TOKEN" | docker login ghcr.io -u theoduras --password-stdin
rm -rf /root/repo && git clone --depth 1 -b "${VJ_REF:-develop}" https://github.com/Theoduras/ai-model-chat /root/repo
cd /root/repo/infra/vast/images
for name in ${IMAGES:-gv h3 lora}; do
  say "building $name"
  docker build -f "$name.Dockerfile" -t "ghcr.io/theoduras/vast-$name:latest" . > "/root/build-$name.log" 2>&1 \
    || { say "FAILED $name"; tail -30 "/root/build-$name.log" | tee -a /root/build.status; exit 1; }
  say "pushing $name"
  docker push -q "ghcr.io/theoduras/vast-$name:latest" >> "/root/build-$name.log" 2>&1
  docker image rm "ghcr.io/theoduras/vast-$name:latest" >/dev/null; docker builder prune -af >/dev/null
  say "done $name"
done
say "ALL DONE"
