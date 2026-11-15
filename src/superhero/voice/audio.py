"""Audio helpers that need no audio device: format conversion and endpointing."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

SAMPLE_RATE = 16_000


def to_mono_16k(pcm: bytes, rate: int, channels: int) -> NDArray[np.float32]:
    """Signed 16-bit interleaved PCM -> mono float32 in [-1, 1] at 16 kHz."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % (2 * channels)], dtype=np.int16)
    audio = samples.reshape(-1, channels).mean(axis=1).astype(np.float32) / 32768.0
    if rate == SAMPLE_RATE or audio.size == 0:
        return audio
    # Linear resampling is adequate for speech recognition input.
    target = np.arange(0, audio.size, rate / SAMPLE_RATE)
    return np.interp(target, np.arange(audio.size), audio).astype(np.float32)


def rms(audio: NDArray[np.float32]) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


@dataclass
class Endpointer:
    """Decides when an utterance is over from a stream of chunk loudness values.

    Speech is anything clearly above the running noise floor. Recording ends
    after ``silence_s`` without speech once some speech was heard, after
    ``no_speech_s`` if nobody spoke at all, or at ``max_s`` regardless.
    """

    silence_s: float
    max_s: float
    no_speech_s: float = 6.0
    _floor: float = field(default=0.005, init=False)
    _elapsed: float = field(default=0.0, init=False)
    _last_speech: float | None = field(default=None, init=False)

    def update(self, level: float, seconds: float) -> bool:
        """Feed one chunk; returns True when recording should stop."""
        self._elapsed += seconds
        if level > max(0.012, self._floor * 3.0):
            self._last_speech = self._elapsed
        # The floor falls quickly and rises slowly: pauses between words reset it, while a
        # steady noise (a fan) is absorbed within seconds instead of counting as speech.
        tau = 0.5 if level < self._floor else 10.0
        self._floor += min(1.0, seconds / tau) * (level - self._floor)
        if self._elapsed >= self.max_s:
            return True
        if self._last_speech is None:
            return self._elapsed >= self.no_speech_s
        return self._elapsed - self._last_speech >= self.silence_s
