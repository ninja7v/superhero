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
- Key chords are always released, even on error. Invalid key names are rejected before anything
  is pressed.
- Gestures are muted during calibration.

## Platform layer

`InputBackend` has six methods: `move`, `button`, `scroll`, `keys`, `type_text`, `close`.
Positions are passed as (display, normalised x, y), and `Display.native_point` converts them to
each platform's units. On Wayland, the RemoteDesktop portal is used, with a restore token
readable only by the user. The permission prompts are the operating system's own. When a
capability is refused, the Home page says so and everything else keeps working.

## Overlay

A frameless, translucent, click-through window covers the active display. Each update repaints
only the pointer's area. The pointer has a light and a dark outline, so it stays visible on any
background. Where the OS allows it, its colour adapts to what is behind it.

## Voice

The microphone stays closed outside voice mode. When the mouth *starts* to open, recording
begins into a buffer, so the first word is not cut off when the hold completes. Recording ends
on silence, a time limit, or the gesture again. The text is matched against the command table.
Anything that isn't a command is typed as dictation. Window commands use the desktop's own
shortcuts, so apps still ask about unsaved work.

## AI commands

Off by default. An utterance that starts with "computer" and isn't a built-in command is sent to
a local Ollama model. The plan it returns must pass strict checks: only `key`, `type`, `launch`,
`scroll`, `click` and `wait` steps, known key names, and at most ten steps. The model has no
tools and cannot access the desktop. The plan is shown for twelve seconds and runs only after a
click gesture or "yes". By default, servers on other machines are refused.

## Configuration

Each profile is one JSON file in the platform's config directory. When loading, unknown keys are
ignored, missing ones get their defaults, and every value is clamped to `config.RANGES` and
`config.CHOICES`. A file that cannot be parsed is renamed to `*.json.bad`, so it is never
silently overwritten. Calibrations are stored separately, one per display and camera.

## Privacy

- Camera frames and audio stay in memory only, and are discarded.
- The network is used only to download the models on first use (all checksum-verified) and, if
  AI commands are on, to reach the local Ollama server. There is no telemetry.
- The overlay and the tray icon show when the camera is in use and when the microphone is
  listening.
