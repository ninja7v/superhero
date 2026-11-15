"""Local speech-to-text with faster-whisper (CTranslate2, CPU, int8).

The model is loaded on first use. Its first download comes from the Hugging
Face hub; after that recognition works offline. Audio never leaves the machine.
"""

from __future__ import annotations

import threading

import numpy as np
from numpy.typing import NDArray


class Transcriber:
    def __init__(self, model: str = "base") -> None:
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        """Whether the model is in memory; the first load may download it."""
        return self._model is not None

    def transcribe(self, audio: NDArray[np.float32], language: str = "en") -> str:
        """Recognise 16 kHz mono float audio. Silence yields an empty string."""
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            segments, _info = self._model.transcribe(
                audio,
                language=language or None,
                beam_size=1,
                vad_filter=True,  # bundled Silero VAD trims silence and reduces hallucination
                condition_on_previous_text=False,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
