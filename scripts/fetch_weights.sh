#!/usr/bin/env bash
# Fetch model weights (not committed). Depth Anything V2 small, ONNX export by fabio-sim (Apache-2.0).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p weights
f=weights/depth_anything_v2_vits.onnx
[ -s "$f" ] || curl -fL -o "$f" https://github.com/fabio-sim/Depth-Anything-ONNX/releases/download/v2.0.0/depth_anything_v2_vits.onnx
sha256sum "$f"
