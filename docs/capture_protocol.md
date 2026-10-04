# Capture protocol

One recording per home. The plan is stitched from a single ARKit session, so walking from room
to room without stopping is what puts every room in the same frame.

## Which tier a device gets

| Device | Tier | Record with | What the pipeline gets |
|---|---|---|---|
| iPhone 12 Pro or later Pro, iPad Pro 2020 or later | LiDAR | Stray Scanner | depth, confidence, ARKit poses, video |
| Other iPhones and Android phones | video or photo | the camera app | video or photos only, no depth |

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
