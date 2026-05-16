#!/usr/bin/env python3
"""Transcribe one audio file to stdout using faster-whisper.

Tuning via env vars (set in ~/.local/bin/whisper-dictate):
  WD_MODEL    model name           (default: large-v3)
  WD_DEVICE   cuda | cpu           (default: cuda, falls back to cpu on error)
  WD_COMPUTE  auto|int8|float16... (default: auto -- CT2 picks fastest supported)
  WD_BEAM     beam size            (default: 1, greedy = snappy for dictation)
  WD_LANG     force language code  (default: empty = auto-detect DE/EN/...)
"""
import os
import sys

from faster_whisper import WhisperModel

audio = sys.argv[1]
model_name = os.environ.get("WD_MODEL", "large-v3")
beam = int(os.environ.get("WD_BEAM", "1"))
lang = os.environ.get("WD_LANG", "").strip() or None


def transcribe(device, compute):
    model = WhisperModel(model_name, device=device, compute_type=compute)
    segments, _info = model.transcribe(
        audio,
        language=lang,
        beam_size=beam,
        vad_filter=True,                 # trim silence -> robust on short clips
        condition_on_previous_text=False,  # less hallucination on short dictation
    )
    return "".join(s.text for s in segments)


want_device = os.environ.get("WD_DEVICE", "cuda")
want_compute = os.environ.get("WD_COMPUTE", "auto")
try:
    text = transcribe(want_device, want_compute)
except Exception as exc:  # GPU/CUDA/cuDNN problem -> CPU fallback
    sys.stderr.write(f"[whisper-dictate] {want_device} path failed: {exc!r}\n")
    sys.stderr.write("[whisper-dictate] falling back to CPU\n")
    text = transcribe("cpu", "int8")

sys.stdout.write(text.strip())
