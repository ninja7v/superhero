# Platforms

A platform counts as supported only after a person has run the
[bring-up checklist](#bring-up-checklist) on it. Passing CI is not enough. Run
`superhero doctor` first on any machine: it lists displays, cameras, microphones, input control
and models, and changes nothing.

## Status

| | Linux Wayland (GNOME) | Linux X11 | Windows | macOS |
|---|---|---|---|---|
| Camera, gaze, overlay | **Works** | Not run | Not run | Not run |
| Click and type injection | **Works** (desktop portal) | Not run | Not run | Not run |
| Adaptive pointer colour | No (Wayland blocks screen reading) | Expected | Expected | Needs Screen Recording |
| Tests and packaged build | **Pass** | In CI (Xvfb) | In CI | In CI |

## Differences handled in code

| | Windows | macOS | Linux X11 | Linux Wayland |
|---|---|---|---|---|
| Input | `SendInput` (pynput) | Quartz (pynput) | XTest (pynput) | RemoteDesktop portal |
| Permission | None (blocked for admin windows) | Accessibility, plus Input Monitoring for the shortcut | None | Consent dialog, remembered |
| Emergency shortcut | pynput | pynput | pynput | GlobalShortcuts portal (desktop entry installed at start) |
| Pointer units | Native pixels per monitor | Points | Scaled pixels | Relative to the monitor stream |

## Known limitations

- **Wayland**: the overlay runs through XWayland. Compositors without the RemoteDesktop portal
  (most wlroots ones) get tracking but no control. Fractional scaling with mixed-DPI monitors has
  not been tested.
- **KDE**: window shortcuts follow GNOME defaults. Add custom voice commands where they differ.
- **Windows**: clicks cannot reach windows running as administrator.
- **macOS**: the bundle is unsigned, so Gatekeeper blocks it until you allow it manually. When
  run from a terminal, the permissions are granted to the terminal, not to SUPERHERO.
- **Cameras**: the default resolution is 1280x720 (MJPG). SUPERHERO falls back to 640x480 when a
  camera cannot deliver HD at full frame rate.

## Packaging

```bash
uv run --group package python packaging/build.py   # builds dist/superhero/
./dist/superhero/superhero doctor
```

This builds a one-folder PyInstaller bundle, so the LGPL libraries can still be replaced. The
bundle includes `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.txt` and the licence texts of every
package it contains. It is about 0.9 GB and does not include the speech model.

## Bring-up checklist

1. `superhero doctor`: every line should make sense.
2. `uv run pytest`, then the platform checks in [Testing and CI](TESTING.md#platform-matrix).
3. Start SUPERHERO, check that the Camera page shows your face, then calibrate.
4. Is the laser above every window, including full-screen ones, and do clicks pass through it?
5. Turn control on. Does each wink click the right button, *where you look*?
6. Hold your mouth open, then dictate a sentence. Try "copy", "minimize", "switch to <app>".
   Then talk normally and yawn: voice mode should not start.
7. Enable hand tracking. Do pinch, two fingers, open palm and fist fire only when you mean them?
8. Press Ctrl+Alt+Esc while another app has focus.
9. `doctor` lists your windows under *Accessibility*. Turn on *Snap to buttons and links* and
   wink just beside a button: the click should land on it. With two monitors, pick *All displays
   together*, calibrate, and check that the laser and clicks cross between screens.
10. Quit and restart. Are settings, calibration and permissions remembered?
