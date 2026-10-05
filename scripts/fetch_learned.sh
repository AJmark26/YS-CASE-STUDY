#!/usr/bin/env bash
# Learned-model weights for the video and photo tiers (not committed; about 6 GB in the
# Hugging Face cache). MapAnything builds its DINOv2 backbone through torch.hub, so the DINOv2
# repo is cloned into the hub cache too (recon.py pins the branch, so no GitHub API call is made).
set -euo pipefail
hub="${TORCH_HOME:-$HOME/.cache/torch}/hub"
mkdir -p "$hub"
[ -d "$hub/facebookresearch_dinov2_main" ] || git clone -q https://github.com/facebookresearch/dinov2 "$hub/facebookresearch_dinov2_main"
python - <<'PY'
from huggingface_hub import snapshot_download
for repo in ["facebook/map-anything", "Ruicheng/moge-2-vitl-normal"]:
    print(repo, snapshot_download(repo))
PY
# the learned damage detector (Grounding DINO tiny, CLIP ViT-B/32, SAM 2.1 small: about 1.5 GB)
cd "$(dirname "$0")/.." && python -c "from ysplan import damage_learned as d; d._models(); print(d.DINO, d.CLIP, d.SAM)"
