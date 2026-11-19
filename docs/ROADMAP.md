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
| AI commands | Built, off by default | Planned with gemma4 (8B) on CPU: 2 to 4.5 s warm. Never run by voice on a real desktop |
| Emergency stop | Built | Wayland needs `superhero install-desktop` |
| Several monitors as one | Built | Calibration window on each monitor; never run on two real screens |
| Click snapping to buttons and links | Built, off by default | Checked against a real X11 (XWayland) window. Not available on Wayland |
| AI commands know the open windows | Built | Window list checked on GNOME Wayland; never run by voice |
| AI commands press buttons and links by name | Built | Linux only. Listing and pressing checked on a Qt window on GNOME Wayland |
| Launch on start | Built | Linux autostart entry tested; macOS and Windows never run |
| Face position guidance | Built | Live readout on the Home page; thresholds need a real person |
| Windows and macOS | Built | Never run. See [PLATFORMS.md](PLATFORMS.md) |
| Pen "wand" | Experiment | See [PEN_POC.md](PEN_POC.md) |

## Not built yet

- AI commands that read what is written in windows (only window titles and control names are shared)
- Pressing controls by name on macOS and Windows
- Installers, code signing, auto-update

## Next steps

1. **Improve gaze accuracy.** Measure at 1280x720 with the face position readout showing
   *Good*, then tune calibration sampling and filter out uncertain frames.
2. Tune the mouth and hand gestures using the live readouts on the Home page.
3. Get Windows and macOS working in CI, then run the
   [bring-up checklist](PLATFORMS.md#bring-up-checklist) on each.
4. Try *All displays together* on two monitors, and snapping on X11, Windows and macOS.
