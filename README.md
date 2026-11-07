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

## HD-map dataset mirror (current deployment mode)

```sh
kitscenes plan --state /storage/kitscenes-full \
  --roots /mnt/hdd1/kitscenes /mnt/hdd2/kitscenes --mode full --hd-only
kitscenes run --state /storage/kitscenes-full --method wget --jobs 16
```

The deployed plan selects only splits with published HD maps: `train`, `val`, and `overlap_train_val`. At pinned revision `d6e8ee0fccd016d4e7815a52be00d26c0f1785ce`, these contain **673 scenes** (533 + 117 + 23), totaling **3,213,310,238,720 archive bytes** (3.213 TB). Existing archives and partial downloads retain their disks; the remaining archives are balanced across both HDDs, at about 1.607 TB each. Every selected original archive is retained, including every camera, LiDAR, radar and all published annotations. There is no remote tar indexing in full mode. Native wget/curl byte resume and HF's native full-file download are supported (`--method hf`). The default full-mode transport is wget. The two smallest scenes from each of train/val are scheduled first for map review; the remaining archives follow largest-first order. Set `--preview-scenes-per-split 0` for largest-first order throughout. Resumes keep the same review set and retain every partial archive. Verification uses the publisher's full-archive SHA256; successful checks have durable receipts. Existing completed archives are reused.

Only the front camera and metadata are additionally extracted for browsing, avoiding a second full dataset copy. HD-only downloads check for a nonempty map and a geographic map origin before marking extraction complete. Other modalities remain available in the original tars. Full download progress includes transferred bytes, recent/average MB/s, remaining-time estimate, per-archive phase, errors and available disk space. A failed run can resume the same plan without downloading verified archives again.

The authors explicitly withhold maps from `test` and maps/global poses/future data from `test-e2e`; see [Appendix G.1 and Table 11](https://arxiv.org/html/2606.02956v1). These 333 scenes are excluded from the deployed download plan. The advertised 62 km² describes the annotated dataset coverage, including held-out evaluation regions. Omit `--hd-only` only when those sensor-only splits are wanted.

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
| `--splits train val` | Restrict dataset splits; omitted means every published split unless `--hd-only` is set |
| `--hd-only` | Restrict to `train`, `val`, `overlap_train_val`, which publish HD maps; requires metadata extraction |
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
./scripts/run-server.sh --mode full --hd-only
```

The script builds the image and creates two containers: `kitscenes-download` (restart on failure) and `kitscenes-web` (restart unless stopped). It mounts both HDD directories and durable state. The browser service receives read-only dataset/state mounts. `PREVIEW_SCENES_PER_SPLIT` configures the initial review set (default 2). Open `http://SERVER_LAN_IP:8795` from your workstation. Deployment paths and hostnames are parameters; no personal server configuration is embedded in the source.

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

## Preview a mapped scene before its full archive finishes

```sh
kitscenes prepare-preview --state /storage/kitscenes-full --scene SCENE_UUID
```

This reads camera frames and calibration from the already downloaded `.tar.part` prefix and retrieves a bounded tar tail (32 MiB by default) for the HD map, map origin, poses and timestamps. It writes a separate `.preview.json`, with full archive SHA256 explicitly pending; it never marks the archive complete. The regular full downloader continues, verifies the publisher checksum and replaces the preview receipt after extraction. Tail bytes are cached outside the repository. Increase `--tail-mib` if this scene's map is outside the default tail window.

## Browse

- Search scene IDs and filter by split or readiness.
- Play/pause with Space; step with Left/Right or the timeline. Playback loops by default; uncheck `loop` to stop at the last frame.
- Drag/scroll the HD map. `follow ego` is checked by default and preserves a drag offset while the car moves; double-click to recenter. The car follows the published pose and heading, with a schematic 4.7 × 2.35 m footprint in world units. `trip` fits the full route.
- Distance rings have fixed radii every 10 metres around ego, independent of zoom, with distance labels on all four axes. With `follow ego` enabled, labels align with the car's front, right, rear and left; otherwise they align with map north, east, south and west. Toggle `rings · 10 m` directly on the map. Enable `ruler` and click two points to measure planar map distance; `×` clears the measurement.
- Zoom/pan the camera; double-click to reset. Zoom is anchored at the cursor. Geometry is reprojected into a viewport-sized GPU buffer on every view change, keeping line thickness constant and avoiding raster magnification. A paused zoom loads the original camera image.
- **layers** has independent camera and HD map checkboxes for categories and source types. Road geometry and road paintings are enabled by default; signs, lights, poles, other objects, trajectory, FOV and grid are initially hidden. Rings and the car are enabled. When enabled, clicking the trajectory selects its camera frame. FOV rays use camera intrinsics/extrinsics.
- Click a line in either pane to highlight the full source feature and its original vertices in both panes. Its tags open in a floating panel without resizing the views. The compact moment inspector reports source time, pose alignment, XYZ, heading, speed and derived acceleration/yaw rate. Missing HD maps are shown explicitly.
- Inspect original camera matrices in **Calibration**. The **files** tab groups metadata by topic/directory, searches filenames, pages through 100 files at a time, and previews text/YAML/JSON in place. Original files remain downloadable.
- Copy a link to the scene/frame. New scenes appear automatically as downloads finish. A preview upgrades to the verified dataset view without resetting the selected frame.

Camera overlays use the published pinhole calibration and closest available ego pose at each camera timestamp. Geometry behind the near plane is clipped in WebAssembly. This is a geometric inspection view; it does not estimate occlusions from images or LiDAR. Coordinates are transformed using the published map origin and shifted in float64 before GPU projection to avoid precision jitter. Original files remain accessible through a traversal-checked, read-only endpoint.

## Validation

```sh
.venv/bin/pip install pytest httpx
.venv/bin/pytest -q
node --check src/maptokens/kitscenes/web/app.js
node tests/test_view.mjs
# Optional, against the deployed service (requires Playwright and Chromium):
.venv/bin/python tests/browser_smoke.py --url http://SERVER_LAN_IP:8795 --browser /path/to/chromium --test-handoff
.venv/bin/python tests/browser_navigation.py --url http://SERVER_LAN_IP:8795 --browser /path/to/chromium
.venv/bin/python tests/browser_interactions.py --url http://SERVER_LAN_IP:8795 --browser /path/to/chromium
```

Tests cover native wget/curl full-archive resume and SHA256 verification, archive retention, real HTTP range responses from a local fixture server, PAX tar headers, excluded sensor seeks, incorrect range rejection, interrupted-scene recovery, hash repair, exact frame/pose alignment with missing image numbers, and web API file access bounds. For runtime verification inspect `/api/health`, `/api/status`, and a completed scene. The browser exposes `explorerDiagnostics()` with WASM/WebGL readiness, render time, frame number and cache size.
