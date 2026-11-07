#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CLANG=${CLANG:-clang}
WASM_LD=${WASM_LD:-$(command -v wasm-ld-18 || command -v wasm-ld)}
"$CLANG" --target=wasm32 -fuse-ld="$WASM_LD" -O3 -nostdlib -Wl,--no-entry -Wl,--export-memory -Wl,--initial-memory=16777216 -Wl,--max-memory=16777216 src/maptokens/kitscenes/web/project.c -o src/maptokens/kitscenes/web/project.wasm
