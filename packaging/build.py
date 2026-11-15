"""Build a self-contained SUPERHERO folder for the current operating system.

    uv run --group package python packaging/build.py

The result is ``dist/superhero/`` containing the ``superhero`` executable. It is a
one-folder bundle on purpose: Qt and pynput are LGPL, and keeping them as separate,
replaceable files is what their licence asks for. Cross-compiling is not possible;
build on each target OS (CI does).
"""

from __future__ import annotations

import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent

args = [
    str(ROOT / "packaging" / "entry.py"),
    "--name=superhero",
    "--onedir",
    "--noconfirm",
    "--clean",
    f"--distpath={ROOT / 'dist'}",
    f"--workpath={ROOT / 'build'}",
    f"--specpath={ROOT / 'build'}",
    f"--paths={ROOT / 'src'}",
    # Packages that load models, shared libraries or data files by path at run time.
    "--collect-all=mediapipe",
    "--collect-all=faster_whisper",
    "--collect-all=ctranslate2",
    "--collect-all=onnxruntime",
    "--collect-submodules=fovea",
    "--collect-submodules=superhero",
    "--collect-data=superhero",  # UI assets
    "--copy-metadata=fovea-input",
    # pynput picks its backend by platform name at import time.
    "--collect-submodules=pynput",
    # matplotlib must stay: `import mediapipe` imports it unconditionally.
    "--exclude-module=tkinter",
]
if sys.platform == "darwin":
    # A windowed .app is what macOS attributes camera/microphone/accessibility grants to.
    args += ["--windowed", "--osx-bundle-identifier=io.github.superhero.Superhero"]
elif sys.platform == "win32":
    args += ["--console"]  # keep stdout for `doctor` and `bench` until there is a GUI log view

PyInstaller.__main__.run(args)

# Licences of everything the bundle redistributes (LGPL texts and source locations included).
sys.path.insert(0, str(ROOT / "packaging"))
from notices import runtime_components, write_notices  # noqa: E402

components = runtime_components()
targets = [ROOT / "dist" / "superhero"]
if sys.platform == "darwin":
    targets.append(ROOT / "dist" / "superhero.app" / "Contents" / "Resources")
for target in targets:
    missing = write_notices(target, components)
print(f"Wrote notices for {len(components)} components to {', '.join(map(str, targets))}")
for component in missing:
    print(
        f"WARNING: {component.name} {component.version} ({component.licence}) ships no licence "
        f"text; add it before a release: {component.homepage}"
    )

if sys.platform == "darwin":
    # macOS kills an app that touches the camera or microphone without these strings.
    import plistlib

    plist_path = ROOT / "dist" / "superhero.app" / "Contents" / "Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    info["NSCameraUsageDescription"] = (
        "SUPERHERO watches your eyes and face to move its pointer. Video is processed on this "
        "Mac and never stored or sent."
    )
    info["NSMicrophoneUsageDescription"] = (
        "SUPERHERO listens only in voice mode, to type what you say and run spoken commands. "
        "Audio is recognised on this Mac."
    )
    plist_path.write_bytes(plistlib.dumps(info))
