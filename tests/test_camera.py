"""Camera format negotiation, against a stand-in for OpenCV's VideoCapture."""

import cv2
import pytest

from superhero.perception import camera as camera_module
from superhero.perception.camera import Camera, CameraError


class FakeCapture:
    opened = True

    def __init__(self, index):
        self.index, self.props, self.order = index, {}, []

    def set(self, prop, value):
        self.props[prop] = value
        self.order.append(prop)
        return True

    def get(self, prop):
        return self.props.get(prop, 0)

    def isOpened(self):
        return FakeCapture.opened

    def release(self):
        pass


@pytest.fixture
def opened(monkeypatch):
    made = []

    def make(index):
        made.append(FakeCapture(index))
        return made[-1]

    monkeypatch.setattr(cv2, "VideoCapture", make)
    FakeCapture.opened = True
    return made


def test_vga_stays_uncompressed(opened):
    Camera(0, 640, 480, 30, True).connect()
    assert cv2.CAP_PROP_FOURCC not in opened[0].props


def test_hd_asks_for_mjpg_before_the_size(opened):
    """YUYV caps a USB 2 webcam at a few fps above VGA; MJPG keeps 30 (measured)."""
    cam = Camera(1, 1280, 720, 30, True)
    cam.connect()
    capture = opened[0]
    assert capture.props[cv2.CAP_PROP_FOURCC] == cv2.VideoWriter.fourcc(*"MJPG")
    assert capture.order.index(cv2.CAP_PROP_FOURCC) < capture.order.index(cv2.CAP_PROP_FRAME_WIDTH)
    assert (cam.actual_width, cam.actual_height) == (1280, 720)


def test_driver_buffer_queue_is_left_alone(opened):
    """A one-buffer queue measured 18.5 fps instead of 30 on V4L2."""
    Camera(0, 1280, 720, 30, True).connect()
    assert cv2.CAP_PROP_BUFFERSIZE not in opened[0].props


def test_a_camera_that_cannot_open_says_so(opened):
    FakeCapture.opened = False
    with pytest.raises(CameraError, match="Could not open camera 2"):
        Camera(2, 640, 480, 30, True).connect()
    assert camera_module.CameraError is CameraError


def test_hd_that_only_comes_slowly_falls_back_to_vga(opened, monkeypatch):
    """Cameras without MJPG give HD uncompressed at ~5 fps; VGA at 30 tracks better."""

    def slow_hd(self, prop):
        if prop == cv2.CAP_PROP_FPS and self.props.get(cv2.CAP_PROP_FRAME_WIDTH) == 1280:
            return 5.0
        return self.props.get(prop, 0)

    monkeypatch.setattr(FakeCapture, "get", slow_hd)
    cam = Camera(0, 1280, 720, 30, True)
    cam.connect()
    assert len(opened) == 2
    assert (cam.actual_width, cam.actual_height, cam.actual_fps) == (640, 480, 30.0)
    assert cv2.CAP_PROP_FOURCC not in opened[1].props


def test_hd_at_full_rate_is_kept(opened):
    cam = Camera(0, 1280, 720, 30, True)
    cam.connect()
    assert len(opened) == 1
    assert (cam.actual_width, cam.actual_height) == (1280, 720)


def test_the_default_resolution_is_hd():
    from superhero.config import CameraConfig

    assert (CameraConfig().width, CameraConfig().height) == (1280, 720)
