"""Groq-native LLM + STT client helpers.

This module is intentionally parallel to the existing implementation.
It does not modify or import existing call flow modules.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from groq import Groq

log = logging.getLogger("shubham-ai.groq_client")

# Requested models
GROQ_LLM_MODEL = "llama-3.3-70b-versatile"
GROQ_STT_MODEL = "whisper-large-v3"

_groq_client: Optional[Groq] = None


def _get_groq_client() -> Groq:
    """Return a singleton Groq client using GROQ_API_KEY."""
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def generate_ai_response_groq(text: str) -> str:
    """Generate AI response via Groq Llama 3.3 70B Versatile.

    Args:
        text: User/customer utterance.

    Returns:
        Assistant text reply. Returns empty string on blank input.
    """
    prompt = (text or "").strip()
    if not prompt:
        return ""

    started = time.perf_counter()
    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=GROQ_LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise voice-call assistant. "
                        "Answer in 1-2 short phone-friendly sentences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=90,
        )
        out = response.choices[0].message.content or ""
        log.info("Groq LLM latency=%.2fms chars_in=%d chars_out=%d",
                 (time.perf_counter() - started) * 1000, len(prompt), len(out))
        return out.strip()
    except Exception:
        log.exception("Groq LLM call failed")
        raise


def speech_to_text_groq(audio_path: str) -> Dict[str, Any]:
    """Transcribe an audio file with Groq Whisper large v3.

    Args:
        audio_path: Path to local audio file.

    Returns:
        Dict with keys: text, language, model, elapsed_ms.
    """
    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    started = time.perf_counter()
    try:
        client = _get_groq_client()
        with path.open("rb") as fh:
            transcript = client.audio.transcriptions.create(
                file=(path.name, fh.read()),
                model=GROQ_STT_MODEL,
                response_format="verbose_json",
            )

        text = getattr(transcript, "text", "") or ""
        language = getattr(transcript, "language", "") or "unknown"
        elapsed_ms = (time.perf_counter() - started) * 1000

        log.info(
            "Groq STT latency=%.2fms file=%s bytes=%d text_len=%d",
            elapsed_ms,
            path.name,
            path.stat().st_size,
            len(text),
        )
        return {
            "text": text.strip(),
            "language": language,
            "model": GROQ_STT_MODEL,
            "elapsed_ms": round(elapsed_ms, 2),
        }
    except Exception:
        log.exception("Groq STT call failed for %s", audio_path)
        raise
