# KITScenes Field Explorer

Independent, publishable code for full or selective KITScenes Multimodal downloads and a server-hosted browser explorer. The dataset itself is **not** included. This implementation was written independently; private reference implementations and credentials are not vendored.

The explorer shows the front camera, projected Lanelet2 geometry, an interactive HD map, ego trajectory, speed, camera intrinsics/extrinsics, original metadata files, and map feature tags/relations. Projection runs in a compiled WebAssembly kernel; line rendering runs in WebGL2. Camera frames are resized on demand, decoded asynchronously, prefetched, and bounded by a 12-frame browser cache. Server scene caches and image caches are also bounded.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

A precompiled `project.wasm` is included with its C source. Rebuild with `scripts/build-wasm.sh` (Clang 18 and wasm-ld 18), or override the compiler/linker through the script for your installation. The viewer has no CDN, external tile, font, or JavaScript dependency.

## Access and credentials

Accept the [dataset terms](https://huggingface.co/datasets/KIT-MRT/KITScenes-Multimodal) using your Hugging Face account. Supply an authorized token with `HF_TOKEN_FILE` (a private file outside the checkout) or `HF_TOKEN`. Do not commit either. The web service does not receive the token. Neither signed CDN URLs nor authorization headers are written to progress logs.

Dataset terms and access are separate from this project's software license. The authoritative schema and dataset documentation are maintained by [KIT-MRT](https://github.com/KIT-MRT/kitscenes).

## Full dataset mirror (current deployment mode)

```sh
kitscenes plan --state /storage/kitscenes-full \
  --roots /mnt/hdd1/kitscenes /mnt/hdd2/kitscenes --mode full
kitscenes run --state /storage/kitscenes-full --method wget --jobs 16
```

Every original archive is retained, including every camera, LiDAR, radar and all published annotations. The pinned 1006-archive manifest is about 4.576 TB. LPT placement assigns about 2.288 TB to each HDD (the planned difference is 23 MB). There is no remote tar indexing in full mode. Native wget/curl byte resume and HF's native full-file download are supported (`--method hf`). The default full-mode transport is wget. Verification uses the publisher's full-archive SHA256; successful checks have durable receipts. Existing completed archives are reused.

Only the front camera and metadata are additionally extracted for browsing, avoiding a second full dataset copy. Other modalities remain available in the original tars. Full download progress includes transferred bytes, recent/average MB/s, remaining-time estimate, per-archive phase, errors and available disk space. A failed run can resume the same plan without downloading verified archives again.

## Selective downloads (optional)

```sh
export HF_TOKEN_FILE=/private/location/hf_token
kitscenes plan --state /storage/kitscenes-state \
  --roots /mnt/hdd1/kitscenes /mnt/hdd2/kitscenes \
  --cameras camera_ring_front
kitscenes benchmark --state /storage/kitscenes-state
kitscenes run --state /storage/kitscenes-state --method auto --jobs 16
```

The default selects **one camera: `camera_ring_front`**, plus all files outside raw camera/LiDAR/radar directories, including labels, Lanelet2 maps, calibration, poses and sensor timestamps. Raw CAN / GNSS are retained if present; their absence is reported explicitly in the explorer. A pose file is not claimed to be raw GNSS or CAN.

Other options at planning time:

| Option | Effect |
| --- | --- |
| `--splits train val` | Restrict dataset splits; omitted means every published split |
| `--scenes UUID UUID` | Restrict scene IDs |
| `--limit 2` | Select the two smallest matching archives for a smoke run |
| `--cameras camera_base_front_center` | Choose the other forward camera instead |
| `--cameras` | No raw camera images; metadata only |
| `--no-metadata` | Disable the default metadata selection |
| `--include 'annotations/*'` | Include matching paths explicitly; repeatable |
| `--exclude 'async/*'` | Exclude matching paths; takes priority; repeatable |
| `--mode full` | Fetch and retain complete original tar archives, verifying publisher SHA256; extract the chosen subset for the explorer |
| `--revision COMMIT` | Pin a particular HF dataset revision |

Create a new state directory to change selection or revision. Plans are immutable by default. The initial LPT plan balances archive sizes; selective downloads refine placement using exact retained file sizes once each remote tar index is known. Durable allocations preserve placement across restarts. Existing completed scenes are never downloaded again.

### Why HTTP Range

The publisher distributes entire scenes as uncompressed tar files. Standard HF downloads operate at the archive level. This tool traverses tar headers with HTTP Range, seeks past unwanted sensor payloads, then retrieves only selected file bodies. A 513-byte lookahead combines tarfile's boundary check with the next header, reducing small requests. Tar indexes and periodic index checkpoints are persistent. Payload transports (`python`, `curl`, `wget`) are benchmarked on the same selected camera file; indexing always uses a persistent Python HTTP connection. Missing/incorrect `206 Content-Range` responses are rejected.

Tar indexing can dominate elapsed time even while very little data is transferred. A low network MB/s value during the `index` phase is expected. Use `full` mode if whole archive transfer is preferable for your network or future modalities; the complete tars are retained to avoid another transfer. A transport failure never silently switches to downloading every sensor.

Selective integrity: HTTPS, exact range/length validation, tar header checksums, and per-file local SHA256 receipts. The **publisher's full-archive SHA256 cannot be verified without downloading the entire archive**. This limitation is recorded in every selective scene receipt. On resume, interrupted scenes verify retained files before skipping them. `.part` files resume by byte offset. Full mode verifies the original publisher checksum before extraction.

## Run on a storage server in Docker

Sync this repository to the server, then run from its checkout:

```sh
export DATA_A=/mnt/hdd1/kitscenes
export DATA_B=/mnt/hdd2/kitscenes
export STATE_DIR=/persistent/location/kitscenes-state
export HF_TOKEN_FILE=/private/location/hf_token
export PORT=8795 JOBS=16
./scripts/run-server.sh --mode full
```

The script builds the image and creates two containers: `kitscenes-download` (restart on failure) and `kitscenes-web` (restart unless stopped). It mounts both HDD directories and durable state. The browser service receives read-only dataset/state mounts. Open `http://SERVER_LAN_IP:8795` from your workstation. Deployment paths and hostnames are parameters; no personal server configuration is embedded in the source.

The web service is intended for the requested LAN access. Use your existing reverse proxy/access controls if exposing it beyond that network.

```sh
docker logs --tail 3 kitscenes-download
docker ps --filter name=kitscenes
# Download logs, progress and benchmark:
cat "$STATE_DIR/status.json"
cat "$STATE_DIR/benchmark.json"
# Stop / resume the same plan:
docker stop kitscenes-download
docker start kitscenes-download
```

Durable state contains `plan.json`, the pinned CSV manifest, `allocations.json`, `indexes/`, `benchmark.json`, `status.json`, and `download.jsonl`. Scene directories contain `.files.json` (local SHA256 receipts) and `.complete.json`. Logs include transferred bytes, instantaneous/average MB/s, active phase, tar traversal position, retained bytes, completion counts, errors, and disk free space. Docker's own logs are rotated.

When deploying changed source, stop and remove only these two project containers, rebuild, then run the script again with the same state/data mounts. State and downloaded files are preserved. No existing container is replaced implicitly.

## Browse

- Search scene IDs and filter by split or readiness.
- Play/pause with Space; step with Left/Right or the timeline.
- Drag/scroll the HD map; double-click to follow ego again.
- Zoom/pan the camera; double-click to reset.
- Toggle map classes in **Map layers**; camera overlays update together.
- Inspect original camera matrices in **Calibration** and all metadata in **Files & labels**.
- Copy a link to the scene/frame. New scenes appear automatically as downloads finish.

Camera overlays use the published pinhole calibration and closest available ego pose at each camera timestamp. Geometry behind the near plane is clipped in WebAssembly. This is a geometric inspection view; it does not estimate occlusions from images or LiDAR. Coordinates are transformed using the published map origin and shifted in float64 before GPU projection to avoid precision jitter. Original files remain accessible through a traversal-checked, read-only endpoint.

## Validation

```sh
.venv/bin/pip install pytest httpx
.venv/bin/pytest -q
node --check src/maptokens/kitscenes/web/app.js
```

Tests cover native wget/curl full-archive resume and SHA256 verification, archive retention, real HTTP range responses from a local fixture server, PAX tar headers, excluded sensor seeks, incorrect range rejection, interrupted-scene recovery, hash repair, exact frame/pose alignment with missing image numbers, and web API file access bounds. For runtime verification inspect `/api/health`, `/api/status`, and a completed scene. The browser exposes `explorerDiagnostics()` with WASM/WebGL readiness, render time, frame number and cache size.
