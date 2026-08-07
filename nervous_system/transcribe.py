#!/usr/bin/env python3
"""transcribe — local, private audio->text for the operator's voice journal.

Runs entirely on the Mac Mini via faster-whisper (CTranslate2). No audio or
transcript leaves the machine except as the operator directs. This module is the
audio->text stage only; entity extraction + the life-graph write (wingmen-personal
DB) are a separate, operator-gated stage that switches on once the personal DB
connection string is placed in ~/.wingmen-personal/.env.

Usage:
    python -m nervous_system.transcribe <audio-file> [--model small.en] [--save]

--save writes the transcript into the personal perimeter (~/.wingmen-personal/
transcripts/), never into any repo.
"""
import os

# CTranslate2 and other numeric libs can each link an OpenMP runtime; on macOS
# that trips "OMP Error #15" and aborts. This is the documented workaround and
# must be set before faster_whisper (ctranslate2) is imported.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "4")

import sys
import argparse
from datetime import datetime, timezone

# Bias recognition toward the operator's recurring proper nouns — small.en
# otherwise mangles these (Wingmen->Wingman, Nahar->Nahr, COSEM->Qosm).
DOMAIN_VOCAB = (
    "Context: Wingmen, ihsanos, irsyad, COSEM, ADCDA, shipforge, storefront, "
    "Gazzabyte, SushiTei, Nahar, Desmond, Musa, Tabung, branditqr, Vercel, Supabase."
)

# Default model: small.en balances accuracy and speed for English journaling on
# CPU. Override with --model (e.g. medium.en for higher accuracy, base.en for speed).
DEFAULT_MODEL = "small.en"
PERSONAL_DIR = os.path.expanduser("~/.wingmen-personal")
TRANSCRIPTS_DIR = os.path.join(PERSONAL_DIR, "transcripts")

_MODEL_CACHE = {}


def _get_model(model_size):
    from faster_whisper import WhisperModel

    if model_size not in _MODEL_CACHE:
        # int8 on CPU is fast and accurate enough for speech; model downloads once
        # and is cached under ~/.cache/huggingface.
        _MODEL_CACHE[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _MODEL_CACHE[model_size]


def transcribe(audio_path, model_size=DEFAULT_MODEL):
    """Transcribe an audio file to text. Returns (text, info_dict)."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(audio_path)
    model = _get_model(model_size)
    segments, info = model.transcribe(
        audio_path, vad_filter=True, beam_size=5, initial_prompt=DOMAIN_VOCAB
    )
    parts = [seg.text.strip() for seg in segments]
    text = " ".join(p for p in parts if p).strip()
    return text, {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration_sec": round(info.duration, 1),
        "model": model_size,
    }


def save_transcript(text, info, source_name):
    """Persist a transcript into the personal perimeter (never a repo)."""
    os.makedirs(TRANSCRIPTS_DIR, mode=0o700, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.splitext(os.path.basename(source_name))[0]
    out = os.path.join(TRANSCRIPTS_DIR, f"{ts}_{base}.txt")
    with open(out, "w") as f:
        f.write(text + "\n")
    os.chmod(out, 0o600)
    return out


def main():
    ap = argparse.ArgumentParser(description="Local private audio->text (voice journal).")
    ap.add_argument("audio", help="path to an audio file (ogg/opus/m4a/mp3/wav/aiff)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"whisper model (default {DEFAULT_MODEL})")
    ap.add_argument("--save", action="store_true", help="save transcript to the personal perimeter")
    args = ap.parse_args()

    text, info = transcribe(args.audio, args.model)
    print(f"[{info['language']} p={info['language_probability']} "
          f"{info['duration_sec']}s {info['model']}]", file=sys.stderr)
    print(text)
    if args.save:
        out = save_transcript(text, info, args.audio)
        print(f"saved -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
