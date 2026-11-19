# Architecture

One Python process with two long-lived threads, and data flowing one way.

```text
 perception thread                         UI thread (Qt)
 ─────────────────                         ──────────────
 Camera ──► Fovea CaptureSession           Controller
            (latest frame only)              │
              │                              ├─► InteractionEngine ──► InputBackend
              ▼                              │      (meaning + safety)   ├ PynputInput  (Win/mac/X11)
 MediaPipe landmarks (once per frame)        │                           └ PortalInput  (Wayland)
              │                              ├─► Overlay (laser, status)
     ┌────────┼──────────────┐               ├─► CalibrationWindow
     ▼        ▼              ▼               ├─► Panel (settings)
 Fovea gaze  MouthOpen    HeadGesture        └─► VoiceSession ──► speech thread
 + blinks    Detector     Detector                  microphone        faster-whisper
     └────────┴──────────────┘                                            │
              │  events (frozen dataclasses)                               ▼
              └───────────── Qt signal ──────────►           InteractionEngine.handle_utterance
```

| Concern | Where |
|---|---|
| Camera and landmarks | `perception/camera.py`, `perception/pipeline.py` |
| Gaze, blinks, filtering, calibration | Fovea |
| Mouth, head and hand gestures | `perception/face_gestures.py`, `perception/hands.py` |
| Meaning and safety | `interaction/engine.py` |
| Gesture → action bindings | `config.GestureConfig.bindings` |
| Desktop input | `platform/`, behind the `InputBackend` protocol |
| Voice | `voice/` |
| AI commands | `ai/planner.py` proposes a plan; the engine confirms and runs it |
| Open windows, click snapping | `platform/accessibility.py` (AT-SPI, AX, UI Automation) |

**Why Python.** Fovea, the gaze engine, is written in Python, and running it in the same process
gives the gesture detectors the landmarks for free. The real per-frame work is native code
(MediaPipe inference, about 14 ms), so Python adds very little: capture to pointer takes 13 ms
median, against a 100 ms budget. Since perception only emits events, it could move to a native
process later without changing the rest.

## Events

Perception emits immutable, timestamped events and doesn't know what they will be used for.
SUPERHERO uses Fovea's event types (`GazePoint`, `TrackingState`, `Wink`, `DoubleBlink`,
`LongBlink`, `Fixation`, `Gesture`…) and adds just one, `PipelineStatus`. A gesture *fires* on
phase `ENDED`. `STARTED` is used to open the microphone early. To add a tracker, emit events from
the perception thread. Nothing downstream has to change.

## Perception thread

For each frame, the thread takes the newest frame (older ones are dropped, so there is never a
backlog), runs landmark inference once, and feeds Fovea plus the mouth and head detectors. It
then emits the batch. Hand tracking, when enabled, runs after that, so it never delays the
laser.

If the camera fails, or the face model cannot load, SUPERHERO reports it and retries. An
unexpected error restarts tracking instead of ending the thread.

**Camera format.** The default is 1280x720 in MJPG, since accuracy depends on how many pixels
cover the iris. If the camera cannot reach at least 75% of the requested frame rate at that
size, SUPERHERO falls back to 640x480 and says so. Sizes up to 3840x2160 are offered, but they
only help when the face is small in the frame, because MediaPipe always works on a 256x256 crop
of the face.

## Safety rules (`InteractionEngine`)

The engine is plain Python with no Qt, so it can be tested thoroughly.

- Nothing is injected unless control is **armed**. Control starts disarmed and needs a
  calibration to turn on.
- Gaze moves the laser, not the system cursor, unless the user turns that on.
- A click needs a confident gaze sample less than 0.5 s old. Otherwise the gesture is dropped.
- Losing tracking, disarming or the emergency stop releases any held drag. An injection error
  disarms.
- Switching camera, resolution or display loads that combination's own calibration. If it has
  none, control is turned off. Changing the monitor layout also turns control off.
- A launch or plan checks the stop between steps, and never holds the engine's lock while it
  waits, so the emergency stop takes effect at once.
- Permission that arrives during a calibration arms control only when it closes.
- Key chords are always released, even on error. Invalid key names are rejected before anything
  is pressed.
- Gestures are muted during calibration.

## Platform layer

`InputBackend` has six methods: `move`, `button`, `scroll`, `keys`, `type_text`, `close`.
Positions are passed as (display, normalised x, y), and `Display.native_point` converts them to
each platform's units. On Wayland, the RemoteDesktop portal is used, with a restore token
readable only by the user. The permission prompts are the operating system's own. When a
capability is refused, the Home page says so and everything else keeps working.

## Several monitors

*All displays together* treats every monitor as one display: its bounding box. Gaze is
normalized to that box and calibrated with the chosen layout on each monitor, so no target falls
in a gap between screens of different sizes. `Display.locate` finds the monitor under a point;
input, the overlay and the calibration window all work per monitor. The calibration belongs to
the arrangement, so moving a monitor needs a new one.

## Accessibility

`platform/accessibility.py` lists the open windows (app and title) and the named controls of
the focused one for AI requests, finds the button, link or field under a point, and presses a
control by name when a confirmed plan asks for it. Listing and pressing controls is AT-SPI only
for now, and needs no screen positions, so it works on Wayland. Every call is time-limited: a
listing stops after 0.6 s with what it found. With snapping on, a click probes
the gaze point and two rings around it, within *Snap distance* and 80 ms, and lands just inside
the nearest actionable element. A point already on one is kept. On Linux only the focused
window is searched. Wayland gives no window positions, so snapping is off there. Apps that
publish no accessibility information get ordinary clicks.

## Overlay

A frameless, translucent, click-through window covers each monitor of the active display. Each update repaints
only the pointer's area. The pointer has a light and a dark outline, so it stays visible on any
background. Where the OS allows it, its colour adapts to what is behind it.

## Voice

The microphone stays closed outside voice mode. When the mouth *starts* to open, recording
begins into a buffer, so the first word is not cut off when the hold completes. Recording ends
on silence, a time limit, or the gesture again. The text is matched against the command table.
Anything that isn't a command is typed as dictation. Window commands use the desktop's own
shortcuts, so apps still ask about unsaved work.

## AI commands

Off by default. An utterance that starts with "computer" is sent to a local Ollama model, unless
it is a built-in command ("computer, copy"), which runs at once. In *Commands* mode every
utterance that is not a built-in command goes to the model, without the wake word. With the
request go the open windows and the focused window's control names, unless that is turned off.
They are marked as data, not instructions, and the plan is still validated and confirmed.

The plan must pass strict checks: only `open` (http and https addresses), `launch`, `press` (a
control by name), `type`, `key` (known key names), `scroll`, `click` and `wait` steps, and at
most sixteen. Plans that close windows, delete, or press a control named like *Send*, *Delete*
or *Buy* are marked risky. The model has no tools and cannot access the desktop. A plan is shown
for twelve seconds and runs only after a click gesture or "yes", or at once if the user chose
to confirm only risky plans. A `press` waits up to two seconds for its control to appear. By
default, servers on other machines are refused.

**Speed.** The instructions are identical for every request and come first, so Ollama keeps
them processed; only the desktop and the request are read each time. When the mouth starts to
open, the speech model and the AI model start loading and the desktop is read, so all three
are ready when the request has been recognised. The overlay shows *Thinking…* while the model
works. An emergency stop drops a request in flight.

## Configuration

Each profile is one JSON file in the platform's config directory. When loading, unknown keys are
ignored, missing ones get their defaults, and every value is clamped to `config.RANGES` and
`config.CHOICES`. A file that cannot be parsed is renamed to `*.json.bad`, so it is never
silently overwritten. Calibrations are stored separately, one per display and camera.

## Privacy

- Camera frames and audio stay in memory only, and are discarded.
- The network is used only to download the models on first use (all checksum-verified) and, if
  AI commands are on, to reach the local Ollama server. Window titles and the names of the
  focused window's controls go only to that server.
  There is no telemetry.
- The overlay and the tray icon show when the camera is in use and when the microphone is
  listening.
