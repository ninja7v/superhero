# Related work

No other project we looked at combines webcam eye gaze, face gestures, local voice and desktop
control on all three operating systems. SUPERHERO's gaze engine comes from Fovea. What SUPERHERO
adds is that combination and the desktop integration.

| | Eye gaze with a webcam | Gestures | Voice | Desktop control | Platforms | Licence |
|---|---|---|---|---|---|---|
| **SUPERHERO** | Yes (Fovea) | Face, head, hands | Yes, local | Yes | Linux (Win/mac untested) | GPL-3.0 |
| [Fovea](https://github.com/Wega-Labs/fovea) | Yes | Blink events | No | No | All | Apache-2.0 |
| EyeNav | No, follows the nose | Blink, mouth | No | Yes | Windows | None |
| OptiKey | Needs an IR tracker | Dwell | No | Yes | Windows | GPL-3.0 |
| WebGazer.js | Yes | No | No | Browser only | Browser | GPL-3.0 |
| Project Gameface | No, follows the head | Face | No | Yes | Windows, Android | Apache-2.0 |
| Talon | Needs a Tobii | Noises | Extensive | Yes | All (X11 only on Linux) | Proprietary |
| Windows Eye Control | Needs an IR tracker | Dwell | Separate | Yes | Windows | Proprietary |
| Apple accessibility | iOS/iPadOS only | Face | Yes | Yes | Apple | Proprietary |

**Accuracy.** Infrared trackers reach about 0.5–1°. Webcam methods with calibration reach about
2–5°, which is roughly 100 px on a 27" 1080p screen at 60 cm. SUPERHERO is therefore designed so
that gaze only aims, and a deliberate gesture acts.

**Where others are ahead:** precision (any infrared product), depth of voice control (Talon),
and years of testing with disabled users (OptiKey, Tobii).

No code was taken from the other projects above, apart from Fovea as a dependency. See
[LICENSING.md](LICENSING.md).
