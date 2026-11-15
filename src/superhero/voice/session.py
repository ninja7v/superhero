"""Voice mode: microphone capture, endpointing and transcription.

The microphone is opened only around voice mode. ``arm()`` starts buffering
when the activation gesture *begins* (the mouth starts opening), so by the time
the gesture completes there is already audio to keep and the first word is not
clipped, without an always-on microphone.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from superhero.config import VoiceConfig
from superhero.voice.audio import SAMPLE_RATE, Endpointer, rms, to_mono_16k
from superhero.voice.stt import Transcriber

log = logging.getLogger(__name__)


class VoiceSession(QObject):
    state_changed = Signal(str)  # idle | armed | listening | preparing | transcribing | error
    result = Signal(str, str)  # recognised text, what was done with it

    def __init__(self, config: VoiceConfig, handle_text: Callable[[str], str]) -> None:
        """``handle_text`` runs on a worker thread and returns a short outcome label."""
        super().__init__()
        self._config = config
        self._handle_text = handle_text
        self._transcriber = Transcriber(config.model)
        self._source: QAudioSource | None = None
        self._io = None
        self._rate = SAMPLE_RATE
        self._channels = 1
        self._chunks: deque[np.ndarray] = deque()
        self._endpointer: Endpointer | None = None
        self.state = "idle"
        # An armed microphone that never becomes a recording is closed again.
        self._disarm = QTimer(self, singleShot=True, interval=3000)
        self._disarm.timeout.connect(lambda: self.arm(False))

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def _open(self) -> bool:
        if self._source is not None:
            return True
        devices = QMediaDevices.audioInputs()
        device = next(
            (d for d in devices if d.description() == self._config.microphone),
            QMediaDevices.defaultAudioInput(),
        )
        if device.isNull():
            self._set_state("error")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._rate, self._channels = fmt.sampleRate(), fmt.channelCount()
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None:
            self._close()
            self._set_state("error")
            return False
        self._io.readyRead.connect(self._on_audio)
        return True

    def _close(self) -> None:
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
        self._source = self._io = None

    def close(self) -> None:
        """Release the microphone; a transcription already running still reports."""
        self._disarm.stop()
        self._chunks.clear()
        self._close()
        if self.state in ("armed", "listening", "error"):
            self._set_state("idle")

    def arm(self, armed: bool) -> None:
        """Pre-open the microphone while the activation gesture is in progress."""
        if self.state == "idle" and armed and self._open():
            self._chunks.clear()
            self._set_state("armed")
            self._disarm.start()
        elif self.state == "armed" and not armed:
            self._close()
            self._set_state("idle")

    def toggle(self) -> None:
        """Start listening, or stop and transcribe what was heard."""
        if self.state in ("idle", "armed", "error"):
            if not self._open():
                return
            self._disarm.stop()
            self._keep_prebuffer()
            self._endpointer = Endpointer(self._config.silence_timeout_s, self._config.max_seconds)
            self._set_state("listening")
        elif self.state == "listening":
            self._finish()

    def _keep_prebuffer(self) -> None:
        keep = int(self._config.prebuffer_ms / 1000 * SAMPLE_RATE)
        audio = np.concatenate(list(self._chunks)) if self._chunks else np.zeros(0, np.float32)
        self._chunks.clear()
        if keep and audio.size:
            self._chunks.append(audio[-keep:])

    def _on_audio(self) -> None:
        if self._io is None:
            return
        chunk = to_mono_16k(bytes(self._io.readAll()), self._rate, self._channels)
        if chunk.size == 0:
            return
        self._chunks.append(chunk)
        if self.state == "armed":
            while sum(c.size for c in self._chunks) > 2 * SAMPLE_RATE:
                self._chunks.popleft()
        elif (
            self.state == "listening"
            and self._endpointer is not None
            and self._endpointer.update(rms(chunk), chunk.size / SAMPLE_RATE)
        ):
            self._finish()

    def _finish(self) -> None:
        audio = np.concatenate(list(self._chunks)) if self._chunks else np.zeros(0, np.float32)
        self._chunks.clear()
        self._close()
        self._set_state("transcribing")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray) -> None:
        text, outcome = "", ""
        try:
            if audio.size >= SAMPLE_RATE // 4:
                if not self._transcriber.loaded:
                    self._set_state("preparing")  # first use: loading or downloading the model
                text = self._transcriber.transcribe(audio, self._config.language)
            outcome = self._handle_text(text) if text else "Nothing heard"
        except Exception as exc:
            log.exception("transcription failed")
            outcome = f"Speech recognition failed: {exc}"
        self.result.emit(text, outcome)
        self._set_state("idle")
