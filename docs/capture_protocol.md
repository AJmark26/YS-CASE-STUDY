# Capture protocol

One recording per home. The plan is stitched from a single ARKit session, so walking from room
to room without stopping is what puts every room in the same frame.

## Which tier a device gets

| Device | Tier | Record with | What the pipeline gets |
|---|---|---|---|
| iPhone 12 Pro or later Pro, iPad Pro 2020 or later | LiDAR | Stray Scanner | depth, confidence, ARKit poses, video |
| Other iPhones | video | an app that saves ARKit poses with the video, in Stray Scanner's layout without `depth/` | ARKit poses, video |
| Any phone | photo | the camera app | 2 to 8 stills per room, one folder per room |
| Android phones, or any clip without poses | video without poses (experimental) | the camera app | video only |

The LiDAR tier is the reference. The video and photo tiers are documented with their own
commands in the README.

## LiDAR walkthrough (Stray Scanner)

| Step | Do this | Why the pipeline needs it |
|---|---|---|
| Before | Turn on the lights, open the interior doors, close curtains on bright windows | Doors that are closed split rooms that should connect; bright windows saturate the video used for damage |
| Start | Stand in a doorway and hold the phone still on a textured wall for 2 seconds | ARKit starts tracking cleanly; the first poses anchor the plan |
| Walls | Walk along each wall about 1 to 1.5 m away, phone at chest height and level, at a slow walk | LiDAR depth is most accurate under 3 m; each wall face is located from its own points, so every wall needs to be seen |
| Floor edge | Sweep the line where the floor meets the walls all the way round | The floor plane, and the free floor that separates rooms, come from rays that land on the floor |
| Ceiling | In each room, tilt the phone up 45 to 60 degrees and sweep the ceiling for 5 to 10 seconds | Ceiling height needs ceiling points; without them the plan reports a lower bound, not a value |
| Openings | Face each door and window straight on from 1 to 2 m, with both jambs in view, and tilt from the floor up past the top of the frame | A jamb is measured at every height between 0.3 and 1.9 m; a jamb seen only low down gives a wide interval or no opening at all |
| Damage | Hold the phone 0.5 to 1 m from any stain, crack or mould for 2 seconds, straight on | Damage is unwrapped onto the wall at 1 cm per pixel; close, square views keep the edges sharp |
| Doorways | Turn slowly and pass through doorways without rushing | Fast turns blur the video and leave gaps in the depth |
| Finish | Walk back to where you started and look at the same wall again | The return view closes the loop, which the drift correction uses to remove accumulated error |

About 20 to 40 seconds per room is enough. If the app shows that tracking was lost, walk back
to a place already recorded before going on.

Things that produce false geometry: mirrors and glass walls (they show rooms that are not
there), people walking through the view, and pointing the phone at a bright window for long.

## Getting the recording to the pipeline

Share the recording from Stray Scanner as a .zip, or copy its folder from the Files app, then:

```
python -m ysplan path/to/recording.zip
```

The loader accepts the current export (per-frame intrinsics in `odometry.csv`, PNG depth) and
the older one (9-column `odometry.csv`, `.npy` depth).

## Video walkthrough (iPhone without LiDAR)

Walk exactly as for LiDAR. Depth comes from a learned model looking at the video, so three
things matter more:

| Do this | Why |
|---|---|
| Keep the floor-wall line in view most of the time, phone tilted 10 to 20 degrees down | The floor fixes gravity and the room's extent; predicted depth on a blank wall alone drifts |
| Walk slower than for LiDAR, and turn slowly | One frame in 40 gets a depth map; motion blur ruins it |
| Avoid filling the view with a single blank wall | The model has nothing to judge distance by |

## Photos (any phone)

One folder per room, named after the room, with 4 to 8 photos, plus one photo through each
doorway from each side. The only photo sets tested are stills cut from the sample walkthrough
videos, which follow this protocol only where the video happened to: floor area came within 8%
of LiDAR in 1 of 8 rooms and 30 to 85% short in six, and 3 of 8 rooms were stitched (`docs/benchmark_report.md`, Tiers
against LiDAR).

| Do this | Why |
|---|---|
| Stand in a corner and photograph the opposite corner, then work round the room | Each wall is seen from across the room, so its whole length is in view |
| Hold the phone at chest height, landscape, tilted slightly down so the floor-wall line is in every photo | The floor sets gravity and the room's extent; a room whose floor edge was not seen comes out too small |
| Let each photo share a third to three quarters of its view with one already taken | The photos are placed relative to each other by what they share |
| Do not edit, crop or zoom the photos; send the originals | The lens focal length is read from the photo's EXIF data |

**Doorways.** For every doorway between rooms A and B, take two photos:

1. Stand in A, up to 2 m back from the doorway, and look straight through it into B. Put the
   photo in B's folder and name it `door-from-A_1.jpg` (anything after the underscore).
2. Stand in B and do the same into A: the photo goes in A's folder as `door-from-B_1.jpg`.

Look square through the doorway, within about 20 degrees. The direction into the room is read
from the photo's viewing direction, snapped to the room's walls; a photo more than 25 degrees
off is not used, and a pair taken about 40 degrees off joined the sample bedroom at the wrong
quarter turn before that limit was added. A room without a usable doorway photo from both sides
is laid out beside the plan and listed in `photo.unstitched_rooms`.

Run `python -m ysplan path/to/photos --tier photo`.
