#!/usr/bin/env bash
# Fetch model weights (not committed). Depth Anything V2 small, ONNX export by fabio-sim (Apache-2.0).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p weights
f=weights/depth_anything_v2_vits.onnx
[ -s "$f" ] || curl -fL -o "$f" https://github.com/fabio-sim/Depth-Anything-ONNX/releases/download/v2.0.0/depth_anything_v2_vits.onnx
echo "d2b11a11c1d4a12b47608fa65a17ee9a4c605b55ee1730c8e3b526304f2562be  $f" | sha256sum -c -
