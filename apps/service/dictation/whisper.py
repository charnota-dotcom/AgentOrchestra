"""Local-only voice dictation via faster-whisper.

The orchestrator never streams microphone bytes to a remote service:
the whole point of voice input is that it stays on-device.  We expose
a tiny ``transcribe_file`` function that takes a wav/mp3/m4a path and
returns a transcript string.  The Composer GUI records via Qt's audio
input and writes a temp file before calling this.

Lazy SDK import: when faster-whisper isn't installed, every call
returns a clear NotInstalledError rather than crashing the service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class NotInstalledError(Exception):
    pass


@dataclass
class TranscriptionOptions:
    model_size: str = "base"  # "tiny" / "base" / "small" / "medium" / "large-v3"
    language: str | None = None  # auto-detect if None
    beam_size: int = 5
    vad_filter: bool = True


_model_cache: Any = None
_model_cache_size: str = ""


def _import_sdk() -> Any:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        return WhisperModel
    except ImportError as exc:
        raise NotInstalledError(
            "faster-whisper not installed; install with `pip install faster-whisper`"
        ) from exc


def _load_model(size: str) -> Any:
    global _model_cache, _model_cache_size
    WhisperModel = _import_sdk()
    if _model_cache is not None and _model_cache_size == size:
        return _model_cache
    log.info("loading whisper model: %s", size)
    _model_cache = WhisperModel(size, device="cpu", compute_type="int8")
    _model_cache_size = size
    return _model_cache


def transcribe_file(
    audio_path: Path,
    options: TranscriptionOptions | None = None,
) -> str:
    """Transcribe a single audio file to text.  Synchronous; the GUI
    runs this in a Qt thread pool to avoid blocking the event loop.
    """
    opts = options or TranscriptionOptions()
    model = _load_model(opts.model_size)
    segments, _info = model.transcribe(
        str(audio_path),
        language=opts.language,
        beam_size=opts.beam_size,
        vad_filter=opts.vad_filter,
    )
    return "".join(seg.text for seg in segments).strip()


def is_available() -> bool:
    try:
        _import_sdk()
        return True
    except NotInstalledError:
        return False
