#!/usr/bin/env bash
# Run on the storage server from a synced checkout. No credentials in this file.
set -euo pipefail
: "${DATA_A:?Set DATA_A to a directory on hdd1}"
: "${DATA_B:?Set DATA_B to a directory on hdd2}"
: "${STATE_DIR:?Set STATE_DIR to a persistent state directory}"
: "${HF_TOKEN_FILE:?Set HF_TOKEN_FILE to a private token file outside the checkout}"
PORT=${PORT:-8795}
JOBS=${JOBS:-16}
METHOD=${METHOD:-auto}
PREVIEW_SCENES_PER_SPLIT=${PREVIEW_SCENES_PER_SPLIT:-2}
mkdir -p "$DATA_A" "$DATA_B" "$STATE_DIR"
docker build -f docker/Dockerfile -t maptokens-kitscenes .
mounts=(-v "$DATA_A:/data/a" -v "$DATA_B:/data/b" -v "$STATE_DIR:/state")
auth=(-v "$HF_TOKEN_FILE:/run/secrets/hf_token:ro" -e HF_TOKEN_FILE=/run/secrets/hf_token)
uid=(--user "$(id -u):$(id -g)")
if [[ ! -f "$STATE_DIR/plan.json" ]]; then
  docker run --rm "${uid[@]}" "${mounts[@]}" "${auth[@]}" maptokens-kitscenes plan --state /state --roots /data/a /data/b "$@"
fi
if [[ ! -f "$STATE_DIR/benchmark.json" ]] && python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["mode"] == "full")' "$STATE_DIR/plan.json"; then
  docker run --rm "${uid[@]}" "${mounts[@]}" "${auth[@]}" maptokens-kitscenes benchmark --state /state
fi
# Existing containers must be explicitly stopped before replacing a deployment.
if ! docker container inspect kitscenes-download >/dev/null 2>&1; then
  docker run -d --name kitscenes-download --restart on-failure:10 "${uid[@]}" "${mounts[@]}" "${auth[@]}" --log-opt max-size=20m --log-opt max-file=3 maptokens-kitscenes run --state /state --jobs "$JOBS" --method "$METHOD" --preview-scenes-per-split "$PREVIEW_SCENES_PER_SPLIT"
fi
if ! docker container inspect kitscenes-web >/dev/null 2>&1; then
  docker run -d --name kitscenes-web --restart unless-stopped "${uid[@]}" -v "$DATA_A:/data/a:ro" -v "$DATA_B:/data/b:ro" -v "$STATE_DIR:/state:ro" -p "$PORT:8795" --log-opt max-size=20m --log-opt max-file=3 maptokens-kitscenes serve --state /state
fi
