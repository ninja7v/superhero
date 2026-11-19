import ipaddress
import os
import socket

import pytest

# The one opt-in to real desktop input, for a disposable machine (see the real_input marker).
REAL_INPUT = "SUPERHERO_ALLOW_REAL_INPUT"
_PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


try:  # pynput connects to the display at import; without one, nothing can be injected anyway
    from pynput import keyboard, mouse
except Exception:
    keyboard = mouse = None


def pytest_collection_modifyitems(config, items):
    if os.environ.get(REAL_INPUT) == "1":
        return
    skip = pytest.mark.skip(reason=f"drives the real pointer; set {REAL_INPUT}=1 to run it")
    for item in items:
        if item.get_closest_marker("real_input"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never touch the real profile or calibration directories from tests."""
    monkeypatch.setenv("SUPERHERO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SUPERHERO_DATA_DIR", str(tmp_path / "data"))
    # Desktop entries and launch at login (platform/launcher.py) live under these.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


def _refuse(what):
    def refused(*_args, **_kwargs):
        raise RuntimeError(f"a test tried to {what}; use a fake instead (tests/conftest.py)")

    return refused


@pytest.fixture(autouse=True)
def no_real_side_effects(request, monkeypatch):
    """Tests run against fakes: no input reaches the desktop, no portal dialog opens, no
    request leaves this machine. Holds even when a test forgets its fake.

    A ``real_input`` test may move the pointer; clicks and keys stay blocked for it too.
    """
    if mouse is not None:
        for name in ("press", "release", "click", "scroll"):
            monkeypatch.setattr(mouse.Controller, name, _refuse("click or scroll for real"))
        for name in ("press", "release", "tap", "type"):
            monkeypatch.setattr(keyboard.Controller, name, _refuse("type for real"))
        if not request.node.get_closest_marker("real_input"):
            position = mouse.Controller.position
            blocked = property(position.fget, _refuse("move the real pointer"))
            monkeypatch.setattr(mouse.Controller, "position", blocked)
            monkeypatch.setattr(mouse.Controller, "move", _refuse("move the real pointer"))

    try:  # Linux only: the RemoteDesktop and GlobalShortcuts portals live on the session bus
        import jeepney.io.blocking
    except ImportError:
        pass
    else:
        # Not inside the try: if the portal module breaks, every test must fail, not run
        # without this guard.
        from superhero.platform import portal

        no_bus = _refuse("open the D-Bus session bus (portal consent dialogs)")
        monkeypatch.setattr(jeepney.io.blocking, "open_dbus_connection", no_bus)
        monkeypatch.setattr(portal, "open_dbus_connection", no_bus)

    import webbrowser

    monkeypatch.setattr(webbrowser, "open", _refuse("open a web page in the real browser"))

    # A proxy would route even 127.0.0.1 requests (the stand-in Ollama) off this machine.
    for name in _PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)

    def offline(real, address_at):
        def local_only(sock, *args):
            address = args[address_at]
            inet = sock.family in (socket.AF_INET, socket.AF_INET6)
            if inet and not _loopback(address[0]):
                raise RuntimeError(f"a test tried to reach {address[0]}; tests stay offline")
            return real(sock, *args)

        return local_only

    resolve = socket.getaddrinfo

    def local_resolve(host, *args, **kwargs):
        if host is not None and not _loopback(host if isinstance(host, str) else host.decode()):
            raise RuntimeError(f"a test tried to resolve {host}; tests stay offline")
        return resolve(host, *args, **kwargs)

    # The address is the first argument, except for sendto(data, [flags,] address).
    for name, address_at in (("connect", 0), ("connect_ex", 0), ("sendto", -1)):
        real = getattr(socket.socket, name)
        monkeypatch.setattr(socket.socket, name, offline(real, address_at))
    monkeypatch.setattr(socket, "getaddrinfo", local_resolve)
