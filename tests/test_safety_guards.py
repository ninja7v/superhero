"""The guard in conftest.py: no test reaches the real desktop, session bus or network."""

import socket
import urllib.request

import pytest

from superhero.platform.displays import Display
from tests import conftest

needs_input_backend = pytest.mark.skipif(
    conftest.mouse is None, reason="no input backend here, so nothing could be injected"
)


@needs_input_backend
def test_real_pointer_clicks_and_keys_are_refused():
    pointer, keys = conftest.mouse.Controller(), conftest.keyboard.Controller()
    for inject in (
        lambda: setattr(pointer, "position", (10, 10)),
        lambda: pointer.move(5, 5),
        lambda: pointer.click(conftest.mouse.Button.left),
        lambda: pointer.scroll(0, 3),
        lambda: keys.press("a"),
        lambda: keys.type("hello"),
    ):
        with pytest.raises(RuntimeError, match="a test tried to"):
            inject()


@needs_input_backend
@pytest.mark.real_input
def test_even_a_real_input_test_cannot_click_or_type():
    with pytest.raises(RuntimeError, match="click or scroll for real"):
        conftest.mouse.Controller().click(conftest.mouse.Button.left)
    with pytest.raises(RuntimeError, match="type for real"):
        conftest.keyboard.Controller().type("x")


def test_desktop_portals_cannot_be_opened():
    portal = pytest.importorskip("superhero.platform.portal")  # Linux only
    with pytest.raises(RuntimeError, match="D-Bus"):
        portal.PortalInput([Display("main", 0, 0, 1920, 1080, primary=True)])
    with pytest.raises(RuntimeError, match="D-Bus"):
        portal.PortalHotkey("ctrl+alt+escape", lambda: None)


def test_nothing_leaves_this_machine():
    with pytest.raises(RuntimeError, match="stay offline"):
        urllib.request.urlopen("https://example.com", timeout=1)
    with socket.socket() as sock, pytest.raises(RuntimeError, match="stay offline"):
        sock.connect_ex(("192.0.2.1", 80))  # TEST-NET-1: never routable anyway
    with socket.socket(type=socket.SOCK_DGRAM) as sock, pytest.raises(RuntimeError):
        sock.sendto(b"x", ("192.0.2.1", 53))
