"""Local transcription via faster-whisper (CTranslate2 backend) — audio never leaves this
machine for meetings captured by audio_capture.py. Model is loaded once per process and
reused across meetings (meeting_watcher.py holds one long-running process), since loading it
per-call would dominate runtime on repeat meetings.

CPU/int8 ballpark (SYSTRAN/faster-whisper benchmarks): "small" model, ~4-8 minutes of
processing for a 30-45 minute meeting on a modern desktop CPU. Bump WHISPER_MODEL to "medium"
or "large-v3" for better accuracy at a proportional speed cost.
"""

from __future__ import annotations

from pathlib import Path

WHISPER_MODEL = "small"

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(wav_path: str | Path) -> str:
    """Returns the transcript as plain text with `[mm:ss] ` timestamp prefixes per segment,
    so meeting_summarize.py's prompt can reference "around the 12 minute mark" if useful."""
    model = _get_model()
    segments, _info = model.transcribe(str(wav_path), beam_size=5)

    lines = []
    for seg in segments:  # segments is a generator — this loop is what actually runs inference
        minutes, seconds = divmod(int(seg.start), 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg.text.strip()}")
    return "\n".join(lines)
