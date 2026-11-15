# Licensing

SUPERHERO is licensed under the **GNU General Public License v3.0** (`GPL-3.0-only`). You can
use, study, change and share it. If you distribute a modified version, you must release its
source under the same licence.

**Commercial licences** are available for uses the GPL does not allow, for example shipping
SUPERHERO inside a closed product. Contributions are accepted only under an agreement that
allows them to be included in those licences.

All of its source code was written for this project. One test fixture
(`tests/fixtures/frontal_30f.jsonl`) was copied unmodified from Fovea under Apache-2.0, and is
credited next to the file and in `NOTICE`.

## Runtime dependencies

| Component | Licence | Used for |
|---|---|---|
| Fovea | Apache-2.0 | Gaze engine |
| MediaPipe, plus the face and hand models (downloaded) | Apache-2.0 | Landmarks |
| OpenCV | Apache-2.0 | Camera |
| Qt 6 / PySide6 | **LGPL-3.0** | User interface, audio |
| pynput, python-xlib | **LGPL** | Input on Windows, macOS and X11 |
| faster-whisper, CTranslate2, ONNX Runtime | MIT | Speech recognition |
| Whisper model (downloaded) | MIT | Speech recognition |
| jeepney, platformdirs | MIT | D-Bus, file paths |
| NumPy | BSD-3-Clause | Maths |
| certifi, tqdm | MPL-2.0 | Indirect dependencies |
| Ollama and its models (optional, installed by you) | MIT; each model has its own licence | AI commands |

`packaging/notices.py` generates the complete list, with licence texts, into every packaged build.

## LGPL components

Qt, pynput and python-xlib may be used in any product as long as users can replace them.
SUPERHERO meets that requirement by shipping them as separate libraries in a one-folder bundle,
never statically linked or merged into a single file. Their licence texts are included.

## Code from other projects

No code from other GPL projects (OptiKey, GazePlay, WebGazer.js) is used, even though their
licences would allow it, because it could not be included in commercial licences. EyeNav and
Computer Pointer Controller have no licence, and Talon is proprietary.

Gaze models trained on Gaze360 or ETH-XGaze inherit non-commercial terms. They are not used, and
should not be bundled.
