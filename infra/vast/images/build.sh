#!/bin/bash
# Run anywhere Docker runs (Linux, WSL, Git Bash with Docker Desktop): builds the three worker
# images and pushes them to ghcr.io/theoduras. Needs GHCR_TOKEN set; ~250 GB free.
set -euo pipefail
say() { echo "$*" | tee -a $HOME/vast-build.status; }
command -v docker >/dev/null || { echo "install Docker first"; exit 1; }
echo "$GHCR_TOKEN" | docker login ghcr.io -u theoduras --password-stdin
rm -rf $HOME/vast-build-repo && git clone --depth 1 -b "${VJ_REF:-develop}" https://github.com/Theoduras/ai-model-chat $HOME/vast-build-repo
cd $HOME/vast-build-repo/infra/vast/images
for name in ${IMAGES:-gv h3 lora}; do
  say "building $name"
  docker build -f "$name.Dockerfile" -t "ghcr.io/theoduras/vast-$name:latest" . > "$HOME/vast-build-$name.log" 2>&1 \
    || { say "FAILED $name"; tail -30 "$HOME/vast-build-$name.log" | tee -a $HOME/vast-build.status; exit 1; }
  say "pushing $name"
  docker push -q "ghcr.io/theoduras/vast-$name:latest" >> "$HOME/vast-build-$name.log" 2>&1
  docker image rm "ghcr.io/theoduras/vast-$name:latest" >/dev/null; docker builder prune -af >/dev/null
  say "done $name"
done
say "ALL DONE"
