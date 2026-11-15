import numpy as np
import pytest

from superhero.voice.audio import SAMPLE_RATE, Endpointer, rms, to_mono_16k


def pcm(samples):
    return (np.asarray(samples) * 32767).astype(np.int16).tobytes()


def test_16k_mono_passes_through():
    audio = to_mono_16k(pcm([0.0, 0.5, -0.5, 1.0]), SAMPLE_RATE, 1)
    assert audio.dtype == np.float32
    assert np.allclose(audio, [0.0, 0.5, -0.5, 1.0], atol=1e-3)


def test_stereo_48k_is_downmixed_and_resampled():
    t = np.arange(48_000) / 48_000
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    stereo = np.stack([tone, tone], axis=1).reshape(-1)
    audio = to_mono_16k(pcm(stereo), 48_000, 2)
    assert abs(audio.size - SAMPLE_RATE) <= 1
    assert abs(rms(audio) - 0.5 / np.sqrt(2)) < 0.01


def test_partial_trailing_frame_is_dropped():
    assert to_mono_16k(pcm([0.1, 0.2, 0.3]) + b"\x01", SAMPLE_RATE, 2).size == 1
    assert to_mono_16k(b"", SAMPLE_RATE, 1).size == 0


def run(endpointer, levels, chunk_s=0.1):
    for i, level in enumerate(levels):
        if endpointer.update(level, chunk_s):
            return round((i + 1) * chunk_s, 2)
    return None


def test_stops_after_trailing_silence():
    speech_then_silence = [0.002] * 5 + [0.2] * 20 + [0.002] * 40
    assert run(Endpointer(silence_s=1.5, max_s=20), speech_then_silence) == pytest.approx(
        4.0, abs=0.11
    )


def test_pauses_between_words_do_not_stop_recording():
    halting = ([0.2] * 5 + [0.002] * 10) * 4  # 1 s pauses, 1.5 s timeout
    assert run(Endpointer(silence_s=1.5, max_s=20), halting) is None


def test_gives_up_when_nobody_speaks():
    assert run(Endpointer(silence_s=1.5, max_s=20), [0.003] * 100) == pytest.approx(6.0, abs=0.11)


def test_hard_limit_on_endless_speech():
    assert run(Endpointer(silence_s=1.5, max_s=5), [0.3] * 100) == pytest.approx(5.0, abs=0.11)


def test_steady_background_noise_is_not_speech():
    # A loud fan raises the floor; it must not keep the recording open forever.
    assert run(Endpointer(silence_s=1.5, max_s=20), [0.05] * 300) < 8.0


def test_closing_a_voice_session_releases_the_microphone():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication

    from superhero.config import VoiceConfig
    from superhero.voice.session import VoiceSession

    _app = QCoreApplication.instance() or QCoreApplication([])
    session = VoiceSession(VoiceConfig(), lambda text: "")

    class Source:
        stopped = False

        def stop(self):
            Source.stopped = True

        def deleteLater(self):
            pass

    session._source, session._io = Source(), object()
    session._set_state("listening")
    session.close()
    assert Source.stopped and session._source is None and session.state == "idle"
