# ysplan: phone capture to dimensioned floor plan

One command turns a phone capture of a home into a stitched, dimensioned floor plan
(`plan.json` plus `plan.png`). Every wall length, floor area, ceiling height and opening width
comes with a 95% interval.

```
python -m ysplan <capture> [-o out_dir] [--tier lidar|video|photo] [--no-drift]
```

The tier is detected from the capture: a Stray Scanner folder with `depth/` is LiDAR, a folder
with poses but no depth (or a bare `.mp4`) is video, and a folder of per-room photo folders is photo.

## Setup (clean Linux or macOS machine, about 5 minutes)

```
git clone https://github.com/AJmark26/YS-CASE-STUDY && cd YS-CASE-STUDY
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
bash scripts/fetch_weights.sh            # monocular depth model for the video tier (100 MB)
```

On a headless Linux box Open3D also needs `apt install libegl1 libgl1`.

## Run on the sample data

Unzip each Stray Scanner capture and point the CLI at the folder that holds `odometry.csv`:

```
python -m ysplan data/<capture_id>                     # LiDAR tier, drift-corrected
python -m ysplan data/<capture_id> --no-drift          # drift ablation: raw ARKit poses
python -m ysplan data/<capture_id> --tier video        # RGB video plus ARKit poses, no depth
python scripts/benchmark.py                            # repeatability, cross-tier, ablation
```

A whole-apartment LiDAR capture (87 s) runs in about 1 to 2 minutes on 4 CPU cores with no GPU.

## How it works (LiDAR tier)

1. **Load** the Stray Scanner capture (`ysplan/io_stray.py`): ARKit poses, depth with
   confidence, and intrinsics rescaled to the depth map.
2. **Drift correction** (`ysplan/drift.py`): the capture is split into 3 s chunks. Each chunk is
   fused into a local cloud, and overlapping chunks are registered with yaw-only point-to-plane
   ICP (gravity from ARKit is trusted). A pose graph is then optimised over odometry and loop
   edges. Relocalisation jumps in ARKit are detected, and the unreliable tail after them is dropped.
3. **Fusion** (`ysplan/fuse.py`): confident depth pixels are averaged into 2 cm voxels. Voxels
   with fewer than 3 hits are dropped, which removes flying pixels and most mirror and glass ghosts.
4. **Plan grids** (`ysplan/grid.py`): the floor comes from the height histogram, and the
   Manhattan yaw from wall normals. 2 cm plan rasters hold wall evidence and floor evidence.
5. **Rooms** (`ysplan/rooms.py`): 2D space carving with LiDAR rays that end on the floor or the
   ceiling marks open floor. Doorways are virtually closed and a watershed splits the rooms.
   Regions seen only through a doorway are reported but kept out of the plan.
6. **Room outlines** (`ysplan/layout.py`): a rectilinear polygon is snapped to wall lines found
   as peaks of the wall-point histograms.
7. **Measurements** (`ysplan/measure.py`): each wall face is located from its own points, and
   corners are moved to where the measured faces meet. The error model is described at the top of
   the file.
8. **Openings** (`ysplan/openings.py`): doors, openings and windows are found where carving rays
   cross a wall line. The width is measured jamb to jamb from wall points.

## Output schema (`plan.json`)

`rooms[]` hold `polygon`, `walls[]` (each with `length_m`, `observed_fraction` and
`face_points`), `floor_area_m2` and `ceiling_height_m`. The ceiling height has a `status` of
`measured` or `not_observed`; when the ceiling wasn't seen, it reports a lower bound instead of
a guess. Every measured value is `{value, sigma, ci95}`. `openings[]` give type, width and the
rooms they connect. `adjacency[]` lists room pairs and the doors between them. `drift` reports
loop closures and the misalignment before and after correction.

## Status

| Tier | State |
|---|---|
| LiDAR | Runs end to end on all three sample captures |
| Video | Runs (monocular depth scaled by triangulation with ARKit poses); accuracy below the gate |
| Photo | In progress |
