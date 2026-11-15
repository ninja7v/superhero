"""Camera access: enumeration and a capture source with negotiated format."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from fovea.webcam.camera import CameraError, Webcam

__all__ = ["VGA", "Camera", "CameraError", "CameraInfo", "list_cameras"]

log = logging.getLogger(__name__)

VGA = (640, 480)
# Below this share of the requested frame rate, a size larger than VGA is given up for VGA.
_SLOW = 0.75


@dataclass(frozen=True)
class CameraInfo:
    index: int  # OpenCV device index
    name: str


def list_cameras() -> list[CameraInfo]:
    """Cameras known to the OS, via Qt Multimedia (no device is opened).

    On Linux the OpenCV index is read from the ``/dev/videoN`` device id. On
    Windows and macOS Qt and OpenCV normally enumerate in the same order, but
    nothing guarantees it, so the preview is the user's confirmation.
    """
    from PySide6.QtMultimedia import QMediaDevices

    cameras = []
    for position, device in enumerate(QMediaDevices.videoInputs()):
        match = re.search(r"/dev/video(\d+)$", bytes(device.id()).decode(errors="replace"))
        index = int(match.group(1)) if match else position
        cameras.append(CameraInfo(index, device.description()))
    return cameras


class Camera(Webcam):
    """Fovea's webcam plus frame-rate negotiation and the format actually granted.

    Cameras treat the requested format as a hint; ``actual_*`` report what the
    driver settled on, which is what the calibration identity must record.
    """

    def __init__(self, index: int, width: int, height: int, fps: int, mirror: bool) -> None:
        super().__init__(index, width, height, mirror)
        self.fps = fps
        self.actual_width = width
        self.actual_height = height
        self.actual_fps = float(fps)

    def connect(self) -> None:
        import cv2

        capture = self._open(self.width, self.height)
        # The driver reports the rate of the format it granted: 5 fps for uncompressed
        # 1280x720 on the development camera. Some cameras offer HD only that way over USB
        # 2.0; VGA at full rate then tracks better. VGA is the fallback, never the first choice.
        granted = float(capture.get(cv2.CAP_PROP_FPS))
        if self.width * self.height > VGA[0] * VGA[1] and 0 < granted < _SLOW * self.fps:
            capture.release()
            log.warning(
                "camera %d gives only %.0f fps at %dx%d; using %dx%d",
                self.device_index, granted, self.width, self.height, *VGA,
            )  # fmt: skip
            capture = self._open(*VGA)
        self._capture = capture
        # The driver's buffer queue is left at its default on purpose. With a single buffer
        # V4L2 cannot fill the next frame while this one is being read, which measured 18.5
        # instead of 30 fps. Freshness comes from the capture thread instead: it reads
        # continuously and only the newest frame is processed, so the queue stays empty.
        self.actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width
        self.actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height
        self.actual_fps = float(capture.get(cv2.CAP_PROP_FPS)) or float(self.fps)

    def _open(self, width: int, height: int):  # -> cv2.VideoCapture
        import cv2

        capture = cv2.VideoCapture(self.device_index)
        if width * height > VGA[0] * VGA[1]:
            # Over USB 2.0 most webcams send uncompressed (YUYV) frames above 640x480 at a
            # fraction of the rate: measured 5.5 fps at 1280x720 against 30 fps as MJPG on the
            # development camera. At 640x480 uncompressed is full rate and has no artefacts.
            # The request is a hint; a backend that does not support it ignores it.
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not capture.isOpened():
            capture.release()
            raise CameraError(
                f"Could not open camera {self.device_index}. Check that it is connected, not "
                "used by another application, and that camera access is allowed."
            )
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        return capture


class StillCamera:
    """A fixed image served at a steady frame rate, in place of a webcam.

    Used by tests and ``superhero bench`` to exercise the whole pipeline on
    machines without a camera. It measures processing, not optics: the gaze it
    produces never moves.
    """

    def __init__(self, image: object, fps: float = 30.0) -> None:
        import time

        self._image = image
        self._interval = 1.0 / fps
        self._next = time.monotonic()
        self._sleep, self._now = time.sleep, time.monotonic

    def read(self) -> object:
        delay = self._next - self._now()
        if delay > 0:
            self._sleep(delay)
        self._next = max(self._next + self._interval, self._now())
        return self._image.copy()  # type: ignore[attr-defined]
