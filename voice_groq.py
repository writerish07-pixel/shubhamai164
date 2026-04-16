"""Groq-parallel voice wrapper.

Uses existing Sarvam TTS implementation internally to avoid breaking behavior,
while adding explicit latency instrumentation for auditing.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from voice import synthesize_speech

log = logging.getLogger("shubham-ai.voice_groq")


def text_to_speech_groq_pipeline(text: str, language: str = "hinglish") -> bytes:
    """Convert text -> audio bytes using current TTS stack with latency logs.

    This keeps TTS provider unchanged (Sarvam via existing `voice.synthesize_speech`)
    while enabling a parallel Groq STT/LLM pipeline.
    """
    payload = (text or "").strip()
    if not payload:
        return b""

    started = time.perf_counter()
    audio = synthesize_speech(payload, language)
    elapsed_ms = (time.perf_counter() - started) * 1000

    if not audio:
        log.warning(
            "TTS returned empty audio | lang=%s chars=%d latency=%.2fms",
            language,
            len(payload),
            elapsed_ms,
        )
        return b""

    log.info(
        "TTS success | lang=%s chars=%d bytes=%d latency=%.2fms",
        language,
        len(payload),
        len(audio),
        elapsed_ms,
    )
    return audio


def text_to_speech_groq_with_metrics(text: str, language: str = "hinglish") -> Dict[str, Optional[float]]:
    """Return both audio and timing metadata for benchmarking/reporting."""
    started = time.perf_counter()
    audio = text_to_speech_groq_pipeline(text, language)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "audio": audio,
        "latency_ms": round(elapsed_ms, 2),
        "audio_bytes": len(audio) if audio else 0,
    }
