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

The video and photo tiers get their best depth from learned models (MapAnything and MoGe-2),
installed separately because they are large (about 6 GB with PyTorch on CPU):

```
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-learned.txt
bash scripts/fetch_learned.sh
```

Without them the video tier falls back to the monocular model above and says so. The default
MapAnything checkpoint is licensed CC-BY-NC (non-commercial use). For a commercial deployment
set `YS_MAPANYTHING=facebook/map-anything-apache` (Apache-2.0); on the sample frames that
checkpoint's depth comes out 27 to 32% short of LiDAR, so it would need a scale correction or
fine-tuning.

## Run on the sample data

Unzip each Stray Scanner capture and point the CLI at the folder that holds `odometry.csv`:

```
python -m ysplan data/<capture_id>                     # LiDAR tier, drift-corrected
python -m ysplan data/<capture_id> --no-drift          # drift ablation: raw ARKit poses
python -m ysplan data/<capture_id> --tier video        # RGB video plus ARKit poses, no depth
python scripts/benchmark.py                            # repeatability, cross-tier, ablation
```

A whole-apartment LiDAR capture (87 s) runs in about 1 to 2 minutes on 4 CPU cores with no GPU.

## Running on a new scan

**LiDAR tier** (an iPhone Pro or iPad Pro with LiDAR):

1. Record with the free Stray Scanner
   app, following [docs/capture_protocol.md](docs/capture_protocol.md): walk slowly along the
   walls at chest height, sweep the floor edge, then tilt up and sweep the ceiling of each room.
2. Get the recording off the phone: in Stray Scanner, open the recording and share it as a .zip,
   or copy its folder from the Files app.
3. Run one command. It takes the .zip, the recording folder or any folder above it:

```
python -m ysplan path/to/recording.zip
```

There are no per-room settings to tune. The output goes to `out/<recording>_lidar/`:

| File | What it is |
|---|---|
| `plan.json` | rooms, walls, openings, ceiling heights and areas, each with a 95% interval |
| `plan.png` | the dimensioned plan |
| `textures/` | each wall, floor and ceiling unwrapped at 1 cm per pixel, damage outlined in red |
| `poses_world.npy`, `points_plan_frame.npz` | drift-corrected camera poses and the fused cloud, for checking |

What the loader expects is a Stray Scanner export: `rgb.mp4`, `depth/` (16-bit PNG in
millimetres, or `.npy` from older app versions), `confidence/`, `odometry.csv` (ARKit poses,
with or without per-frame intrinsics) and `camera_matrix.csv`. Frames without a depth map are
skipped and reported under `capture.notes` in `plan.json`.

`scripts/make_test_capture.py` cuts new captures out of the samples (a shorter walk, one room,
a rotated ARKit world, the older export format, a .zip) to check this before a live run.

## Reproduce every number

With the three sample captures unzipped under `data/` (`1a8384c3f6`, `c7d28f72c6`,
`c00a170fe1`), these regenerate everything under `bench/` (about 30 minutes on 4 CPU cores):

```
for c in 1a8384c3f6 c7d28f72c6 c00a170fe1; do python -m ysplan data/$c -o out/${c}_lidar; done
python -m ysplan data/1a8384c3f6 -o out/1a8384c3f6_lidar_nodrift --no-drift --no-damage
python scripts/benchmark.py out bench                  # repeatability, openings, cross-tier, drift ablation
python scripts/ceiling_repeat.py data/c7d28f72c6 bench/ceiling_repeat_c7d28f72c6.json --run out/c7d28f72c6_lidar
python scripts/fixloop.py --data data --out out        # fix-loop rounds from their git tags
```

The walk-in rehearsal cuts unseen-looking captures out of the samples and scores their plans
against the full captures' plans:

```
python scripts/make_test_capture.py data/c00a170fe1 data/walkin/t1_bedroom --start 16 --end 37 --reframe 37
python scripts/make_test_capture.py data/c7d28f72c6 data/walkin/t2_three_rooms --start 100 --end 141 --old-format --zip
python scripts/make_test_capture.py data/1a8384c3f6 data/walkin/t3_bedroom2 --start 35 --end 54 --reframe 200
python scripts/make_test_capture.py data/c7d28f72c6 data/walkin/t4_ceiling_only --start 140 --end 172 --old-format
for t in t1_bedroom t2_three_rooms.zip t3_bedroom2 t4_ceiling_only; do python -m ysplan data/walkin/$t -o out/walkin/${t%.zip}; done
python scripts/walkin_check.py out/walkin/t1_bedroom:out/c00a170fe1_lidar out/walkin/t2_three_rooms:out/c7d28f72c6_lidar \
    out/walkin/t3_bedroom2:out/1a8384c3f6_lidar out/walkin/t4_ceiling_only:out/c7d28f72c6_lidar --json bench/walkin.json
```

Every `plan.json` validates against [docs/plan.schema.json](docs/plan.schema.json).

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
8. **Openings** (`ysplan/openings.py`): along every wall line of the plan, a door, opening or
   window is a gap in solid wall (wall points at most heights) that carving rays crossed. Each
   jamb is the edge of solid wall, measured per height slice, so clutter beside a door cannot
   move it; its spread over the slices sets the width interval.

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
