# 🔴SUPERHERO

**Look to point. Wink to click. Speak to command.**

Laser in your eyes, magic in your wand, and the force is with you.
SUPERHERO lets you control your computer with an ordinary webcam and microphone. A laser dot
follows your eyes, face and hand gestures click, and your voice types and runs commands. Your
desktop, mouse and keyboard keep working as they do now.

> **Status: early.** It runs on a real webcam, but accuracy is not yet good enough for small
> targets. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Features

- **Gaze laser pointer**: drawn above all windows, and clicks pass through it.
- **Calibration** with 5, 9 or 13 points, plus an accuracy test. Use one monitor or all of them.
- **Click snapping** (optional): a click near a button or link lands on it.
- **Gestures you assign to actions**: winks, blinks, dwell, mouth open, head tilt or nod, and
  hand poses. Actions include click, right click, double click, drag, scroll, voice and pause.
- **Local voice**: dictation, plus commands like "copy", "scroll down" or "switch to Firefox".
  You can add your own.
- **AI commands** (optional, local through Ollama): "computer, open Firefox and search for…".
  The model knows which windows are open. The plan is shown on screen and runs only after you
  confirm it.
- **Safety**: control starts off and needs a calibration. Ctrl+Alt+Esc stops everything.
- **Private**: no frames or audio are stored or uploaded, and there is no telemetry.

## Run it

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run superhero
```

1. **Camera**: check that you can see yourself.
2. **Calibration** → *Calibrate now*: follow the dot with your eyes and keep your head still.
3. **Home**: turn control on and allow the system's permission prompt.
4. Look at something and wink. To speak, hold your mouth open for a second.

On first use, SUPERHERO downloads the face model and the speech model. After that it works
offline. On Wayland, run `uv run superhero install-desktop` once so the emergency shortcut works.
`uv run superhero doctor` shows what your machine supports.

| Problem | Fix |
|---|---|
| *Camera unavailable* | Close any other app using the camera, or pick another camera on the Camera page. |
| Frame rate well under 30 | Add light on your face. |
| *Desktop control: Unavailable* | Approve the system dialog (Wayland), or grant Accessibility (macOS). |
| Gestures do nothing | Check *Live signals* on the Home page. |
| The laser is far from where you look | Check *Face position* on the Home page, then recalibrate. |
| Settings were reset | The profile that could not be read is saved next to it as `<name>.json.bad`. |

## What to expect

A webcam is less precise than an infrared eye tracker. Expect a few degrees of error, about a
thumb's width at arm's length. Even light on your face and a camera at the top centre of the
screen help most. Recalibrate after you move.

## Development

```bash
uv run pytest                                          # no camera needed
uv run superhero bench --image face.jpg --seconds 10   # full pipeline on a photo
```

Gaze estimation comes from [Fovea](https://github.com/Wega-Labs/fovea) (Apache-2.0). SUPERHERO
adds the overlay, gestures, input control, voice and settings.

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap and status](docs/ROADMAP.md)
- [Platforms and packaging](docs/PLATFORMS.md)
- [Testing and CI](docs/TESTING.md)
- [Related work](docs/RELATED_WORK.md)
- [Third-party licences](docs/LICENSING.md)

## Licence

GPL-3.0. Packaged builds include the licence texts of everything they bundle. Commercial
licences, for use outside the GPL's terms, are available from the author,
[Luc Prevost](https://github.com/ninja7v).
