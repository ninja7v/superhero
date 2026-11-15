# Roadmap

**Verified** means it was tested on real hardware. **Built** means it is implemented and covered
by automated tests, but has not been tried with a real camera, person or desktop.

## Current status

| Feature | Status | Notes |
|---|---|---|
| Starts on Linux (GNOME, Wayland) | **Verified** | |
| Works with an ordinary USB webcam | **Verified** | 30 fps at 640x480 and at 1280x720 |
| Calibration | **Verified** | |
| Laser follows gaze | Verified, **poor accuracy** | First measurement: about 25% of the screen at 640x480. Re-measure at 1280x720 |
| Wink to click | **Verified** | |
| Latency | **Verified** | 13 ms median from frame capture to pointer drawn |
| Mouth-open voice activation | Built | Not yet confirmed by a person |
| Dictation and voice commands | Built | Speech recognition checked on a recording |
| Head and hand gestures | Built | Hand thresholds still need tuning on live hands |
| AI commands | Built, off by default | Never run by voice on a real desktop |
| Emergency stop | Built | Wayland needs `superhero install-desktop` |
| Windows and macOS | Built | Never run. See [PLATFORMS.md](PLATFORMS.md) |
| Pen "wand" | Experiment | See [PEN_POC.md](PEN_POC.md) |

## Not built yet

- Gaze across several monitors (the laser works on one chosen display)
- Snapping to UI elements using the accessibility tree
- Giving AI commands knowledge of the screen and windows
- Installers, code signing, auto-update

## Next steps

1. **Improve gaze accuracy.** Measure at 1280x720, then tune calibration sampling, filter out
   uncertain frames, and add camera placement guidance.
2. Tune the mouth and hand gestures using the live readouts on the Home page.
3. Get Windows and macOS working in CI, then run the
   [bring-up checklist](PLATFORMS.md#bring-up-checklist) on each.
4. Support several monitors.
5. Snap to UI elements, and give AI commands context, using the accessibility tree.
