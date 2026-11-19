"""How SUPERHERO is started: its command, its desktop entry, and launch at login.

Launch at login is kept where each system looks for it, not in a profile: an XDG
autostart entry on Linux, a LaunchAgent on macOS, and the per-user ``Run`` key on
Windows. Reading it back from there means the switch always shows what will happen.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from superhero.platform.base import APP_ID, desktop_entry_path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "SUPERHERO"


def launch_command() -> list[str]:
    """The command that starts this installation of SUPERHERO."""
    if getattr(sys, "frozen", False):  # a packaged bundle: the executable is the app
        return [sys.executable]
    script = shutil.which("superhero")
    return [script] if script else [sys.executable, "-m", "superhero"]


def desktop_exec(command: list[str]) -> str:
    """Quote a command for a desktop entry's ``Exec`` key (Desktop Entry spec, "Exec key")."""

    def quote(arg: str) -> str:
        if not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
            return arg
        escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
        return f'"{escaped}"'

    # Then the string-level escaping, which the spec undoes before the quoting above: a
    # backslash is doubled, and a literal percent sign is doubled so it is not a field code.
    return " ".join(quote(arg).replace("\\", "\\\\").replace("%", "%%") for arg in command)


def _desktop_entry(command: list[str], extra: str = "") -> str:
    return (
        "[Desktop Entry]\nType=Application\nName=SUPERHERO\n"
        "Comment=Control your computer with your eyes, face and voice\n"
        f"Exec={desktop_exec(command)}\nTerminal=false\n"
        f"Categories=Utility;Accessibility;\n{extra}"
    )


def install_desktop_entry() -> Path:
    """Install the launcher entry the Wayland global-shortcut portal identifies us by."""
    path = desktop_entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_desktop_entry(launch_command()), encoding="utf-8")
    return path


def autostart_path(platform: str = sys.platform) -> Path:
    """Where launch at login is recorded (on Windows, a registry value instead)."""
    if platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / f"{APP_ID}.desktop"


def autostart_enabled(platform: str = sys.platform) -> bool:
    if platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, _RUN_VALUE)
            return True
        except OSError:
            return False
    path = autostart_path(platform)
    if not path.is_file():
        return False
    if platform == "darwin":
        return True
    # A user can switch an entry off in their desktop's startup settings without deleting it.
    text = path.read_text(encoding="utf-8", errors="replace")
    return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text


def set_autostart(enabled: bool, command: list[str], platform: str = sys.platform) -> None:
    """Start ``command`` at every login, or stop doing so. Raises OSError on failure."""
    if platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(
                    key, _RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(command)
                )
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, _RUN_VALUE)
        return
    path = autostart_path(platform)
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if platform == "darwin":
        agent = {"Label": APP_ID, "ProgramArguments": command, "RunAtLoad": True}
        path.write_bytes(plistlib.dumps(agent))
    else:
        path.write_text(
            _desktop_entry(command, "X-GNOME-Autostart-enabled=true\n"), encoding="utf-8"
        )
