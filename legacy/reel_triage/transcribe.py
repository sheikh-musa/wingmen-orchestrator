from __future__ import annotations


def transcribe(media_or_audio: str) -> str:
    """faster-whisper local transcription. Imported lazily so unit tests and the
    Mac Mini bot host don't require the model/runtime — only the Mac Studio
    worker does."""
    from faster_whisper import WhisperModel

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(media_or_audio)
    return " ".join(seg.text.strip() for seg in segments).strip()
