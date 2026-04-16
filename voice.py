"""
voice.py
Handles Speech-to-Text (Sarvam primary, Deepgram fallback) and Text-to-Speech (Sarvam).

OPTIMIZATIONS:
- 🔥 OPTIMIZATION: Use httpx with connection pooling instead of requests (saves ~200ms per call)
- 🔥 OPTIMIZATION: Async-native TTS and STT functions (no thread pool needed)
- 🔥 OPTIMIZATION: Reduced timeouts from 15-20s to 6-8s (fail fast)
- 🔥 OPTIMIZATION: TTS pace increased to 1.2 for faster, more natural phone speech
- 🔥 OPTIMIZATION: Parallel TTS chunk processing for long text
- 🔥 FIX: Removed synchronous requests — all I/O is now async
"""
import base64, re
import httpx
import asyncio
import config as config

# 🔥 OPTIMIZATION: Persistent HTTP client with connection pooling
# Reuses TCP connections — saves ~100-200ms per request (no new TLS handshake)
_http_client = None

def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            # 🔥 FIX: Removed http2=True (requires h2 package not in requirements.txt)
            # Connection pooling benefits are retained without HTTP/2
        )
    return _http_client


SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


# ── LANGUAGE NORMALISATION ────────────────────────────────────────────────────

def _lang_to_code(language: str) -> str:
    language = (language or "").lower().strip()
    mapping = {
        "hindi":       "hi-IN",
        "hinglish":    "hi-IN",
        "rajasthani":  "hi-IN",
        "english":     "en-IN",
        "hi-in":       "hi-IN",
        "en-in":       "en-IN",
        "hi":          "hi-IN",
        "en":          "en-IN",
    }
    return mapping.get(language, "hi-IN")


def _normalize_lang(code: str) -> str:
    code = code.lower()
    if "en" in code:
        return "english"
    if "hi" in code:
        return "hindi"
    if "raj" in code:
        return "rajasthani"
    return "hinglish"


def _detect_audio_mime(audio_bytes: bytes) -> str:
    if len(audio_bytes) >= 4 and audio_bytes[:4] == b'RIFF':
        return "audio/wav"
    if len(audio_bytes) >= 3 and audio_bytes[:3] == b'ID3':
        return "audio/mpeg"
    if len(audio_bytes) >= 2 and audio_bytes[:2] in (
        b'\xff\xfb', b'\xff\xfa', b'\xff\xf3', b'\xff\xf2'
    ):
        return "audio/mpeg"
    return "audio/wav"


# ── SPEECH TO TEXT ────────────────────────────────────────────────────────────

# 🔥 OPTIMIZATION: Async STT — no thread pool overhead
async def transcribe_audio_async(audio_bytes: bytes, language_hint: str = "hi-IN") -> dict:
    """
    Async version of transcribe_audio.
    Returns {"text": "...", "language": "hindi/english/hinglish", "confidence": float}
    """
    try:
        result = await _sarvam_stt_async(audio_bytes, _lang_to_code(language_hint))
        if result.get("text"):
            return result
    except Exception as e:
        print(f"[Voice] Sarvam STT failed: {e}, trying Deepgram")

    try:
        return await _deepgram_stt_async(audio_bytes)
    except Exception as e:
        print(f"[Voice] Deepgram STT failed: {e}")
        return {"text": "", "language": "unknown", "confidence": 0.0}


# 🔥 OPTIMIZATION: Keep synchronous version for backward compatibility but use httpx
def transcribe_audio(audio_bytes: bytes, language_hint: str = "hi-IN") -> dict:
    """Synchronous wrapper — delegates to sync httpx calls."""
    try:
        result = _sarvam_stt(audio_bytes, _lang_to_code(language_hint))
        if result.get("text"):
            return result
    except Exception as e:
        print(f"[Voice] Sarvam STT failed: {e}, trying Deepgram")

    try:
        return _deepgram_stt(audio_bytes)
    except Exception as e:
        print(f"[Voice] Deepgram STT failed: {e}")
        return {"text": "", "language": "unknown", "confidence": 0.0}


async def _sarvam_stt_async(audio_bytes: bytes, language: str = "hi-IN") -> dict:
    if not config.SARVAM_API_KEY:
        raise ValueError("SARVAM_API_KEY not configured")

    mime = _detect_audio_mime(audio_bytes)
    ext  = "wav" if mime == "audio/wav" else "mp3"

    headers = {"api-subscription-key": config.SARVAM_API_KEY}
    files   = {"file": (f"audio.{ext}", audio_bytes, mime)}
    data    = {
        "model":           "saarika:v2.5",
        "language_code":   language,
        "with_timestamps": "false",
    }

    client = _get_client()
    # 🔥 OPTIMIZATION: Reduced timeout from 15s to STT_TIMEOUT_SEC (default 6s)
    r = await client.post(SARVAM_STT_URL, headers=headers, files=files, data=data,
                          timeout=config.STT_TIMEOUT_SEC)

    if r.status_code != 200:
        print(f"[Voice] Sarvam STT failed: {r.status_code}, response: {r.text[:300]}")
        raise Exception(f"Sarvam STT {r.status_code}: {r.text[:200]}")

    result = r.json()
    return {
        "text":       result.get("transcript", ""),
        "language":   _normalize_lang(result.get("language_code", "hi-IN")),
        "confidence": 0.9,
    }


def _sarvam_stt(audio_bytes: bytes, language: str = "hi-IN") -> dict:
    """Synchronous Sarvam STT for backward compatibility."""
    if not config.SARVAM_API_KEY:
        raise ValueError("SARVAM_API_KEY not configured")

    mime = _detect_audio_mime(audio_bytes)
    ext  = "wav" if mime == "audio/wav" else "mp3"

    headers = {"api-subscription-key": config.SARVAM_API_KEY}
    files   = {"file": (f"audio.{ext}", audio_bytes, mime)}
    data    = {
        "model":           "saarika:v2.5",
        "language_code":   language,
        "with_timestamps": "false",
    }

    # 🔥 OPTIMIZATION: Use httpx sync client with reduced timeout
    with httpx.Client(timeout=config.STT_TIMEOUT_SEC) as client:
        r = client.post(SARVAM_STT_URL, headers=headers, files=files, data=data)

    if r.status_code != 200:
        raise Exception(f"Sarvam STT {r.status_code}: {r.text[:200]}")

    result = r.json()
    return {
        "text":       result.get("transcript", ""),
        "language":   _normalize_lang(result.get("language_code", "hi-IN")),
        "confidence": 0.9,
    }


def _deepgram_stt(audio_bytes: bytes) -> dict:
    """Synchronous Deepgram fallback."""
    if not config.DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY not configured")

    mime_type = _detect_audio_mime(audio_bytes)
    headers   = {
        "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
        "Content-Type":  mime_type,
    }
    params = {
        "model":           "nova-2",
        "language":        "hi",
        "detect_language": "true",
        "smart_format":    "true",
        "punctuate":       "true",
    }

    with httpx.Client(timeout=config.STT_TIMEOUT_SEC) as client:
        r = client.post(
            "https://api.deepgram.com/v1/listen",
            headers=headers, params=params, content=audio_bytes,
        )
    r.raise_for_status()

    data     = r.json()
    channels = data.get("results", {}).get("channels", [{}])
    alts     = channels[0].get("alternatives", [{}])
    lang     = channels[0].get("detected_language", "hi")

    return {
        "text":       alts[0].get("transcript", ""),
        "language":   _normalize_lang(lang),
        "confidence": alts[0].get("confidence", 0.8),
    }


async def _deepgram_stt_async(audio_bytes: bytes) -> dict:
    """Async Deepgram fallback."""
    if not config.DEEPGRAM_API_KEY:
        raise ValueError("DEEPGRAM_API_KEY not configured")

    mime_type = _detect_audio_mime(audio_bytes)
    headers   = {
        "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
        "Content-Type":  mime_type,
    }
    params = {
        "model":           "nova-2",
        "language":        "hi",
        "detect_language": "true",
        "smart_format":    "true",
        "punctuate":       "true",
    }

    client = _get_client()
    r = await client.post(
        "https://api.deepgram.com/v1/listen",
        headers=headers, params=params, content=audio_bytes,
        timeout=config.STT_TIMEOUT_SEC,
    )
    r.raise_for_status()

    data     = r.json()
    channels = data.get("results", {}).get("channels", [{}])
    alts     = channels[0].get("alternatives", [{}])
    lang     = channels[0].get("detected_language", "hi")

    return {
        "text":       alts[0].get("transcript", ""),
        "language":   _normalize_lang(lang),
        "confidence": alts[0].get("confidence", 0.8),
    }


# ── TEXT TO SPEECH ────────────────────────────────────────────────────────────

# 🔥 OPTIMIZATION: Async TTS — eliminates thread pool overhead
async def synthesize_speech_async(text: str, language: str = "hinglish") -> bytes:
    """
    Async convert text -> MP3 audio bytes via Sarvam AI.
    Returns b"" on failure so callers can fall back to <Say>.
    """
    text = re.sub(r'\{[^}]+\}', '', text, flags=re.DOTALL)
    text = re.sub(r'```.*?```',  '', text, flags=re.DOTALL)
    text = text.strip()

    if not text:
        return b""
    if not config.SARVAM_API_KEY:
        return b""

    lang_code = _lang_to_code(language)

    try:
        return await _sarvam_tts_async(text, lang_code)
    except Exception as e:
        print(f"[Voice] Sarvam TTS failed: {e}")
        return b""


def synthesize_speech(text: str, language: str = "hinglish") -> bytes:
    """Synchronous TTS — backward compatible."""
    text = re.sub(r'\{[^}]+\}', '', text, flags=re.DOTALL)
    text = re.sub(r'```.*?```',  '', text, flags=re.DOTALL)
    text = text.strip()

    if not text:
        return b""
    if not config.SARVAM_API_KEY:
        return b""

    lang_code = _lang_to_code(language)

    try:
        return _sarvam_tts(text, lang_code)
    except Exception as e:
        print(f"[Voice] Sarvam TTS failed: {e}")
        return b""


async def _sarvam_tts_async(text: str, language: str = "hi-IN") -> bytes:
    """Async Sarvam TTS with parallel chunk processing."""
    chunks = _split_text(text, max_chars=490)

    headers = {
        "api-subscription-key": config.SARVAM_API_KEY,
        "Content-Type":         "application/json",
    }

    # 🔥 OPTIMIZATION: Process TTS chunks in parallel instead of sequentially
    if len(chunks) == 1:
        return await _tts_single_chunk(chunks[0], language, headers)

    tasks = [_tts_single_chunk(chunk, language, headers) for chunk in chunks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_audio = b""
    for result in results:
        if isinstance(result, Exception):
            print(f"[Voice] TTS chunk failed: {result}")
            # 🔥 FIX: If any chunk fails, discard all and raise so caller falls back to <Say>
            raise result
        all_audio += result

    return all_audio


async def _tts_single_chunk(chunk: str, language: str, headers: dict) -> bytes:
    """Process a single TTS chunk asynchronously."""
    payload = {
        "inputs":               [chunk],
        "target_language_code": language,
        "speaker":              "anushka",
        "model":                "bulbul:v2",
        "pitch":                0,
        "pace":                 1.2,   # 🔥 OPTIMIZATION: Faster pace for natural phone speech (was 1.1)
        "loudness":             1.5,
        "enable_preprocessing": True,
    }

    client = _get_client()
    # 🔥 OPTIMIZATION: Reduced timeout from 20s to TTS_TIMEOUT_SEC (default 5s)
    r = await client.post(SARVAM_TTS_URL, headers=headers, json=payload,
                          timeout=config.TTS_TIMEOUT_SEC)

    if r.status_code != 200:
        print(f"[Voice] Sarvam TTS error: {r.status_code} {r.text[:200]}")
        r.raise_for_status()

    data = r.json()
    audios = data.get("audios") or data.get("audio")
    if not audios:
        raise ValueError("No audio in Sarvam TTS response")

    audio_b64 = audios[0] if isinstance(audios, list) else audios
    return base64.b64decode(audio_b64)


def _sarvam_tts(text: str, language: str = "hi-IN") -> bytes:
    """Synchronous Sarvam TTS — backward compatible."""
    chunks = _split_text(text, max_chars=490)
    all_audio = b""

    headers = {
        "api-subscription-key": config.SARVAM_API_KEY,
        "Content-Type":         "application/json",
    }

    for chunk in chunks:
        payload = {
            "inputs":               [chunk],
            "target_language_code": language,
            "speaker":              "anushka",
            "model":                "bulbul:v2",
            "pitch":                0,
            "pace":                 1.2,   # 🔥 OPTIMIZATION: Faster pace
            "loudness":             1.5,
            "enable_preprocessing": True,
        }

        with httpx.Client(timeout=config.TTS_TIMEOUT_SEC) as client:
            r = client.post(SARVAM_TTS_URL, headers=headers, json=payload)

        if r.status_code != 200:
            r.raise_for_status()

        data = r.json()
        audios = data.get("audios") or data.get("audio")
        if not audios:
            raise ValueError("No audio in Sarvam TTS response")

        audio_b64 = audios[0] if isinstance(audios, list) else audios
        all_audio += base64.b64decode(audio_b64)

    return all_audio


def _split_text(text: str, max_chars: int = 490) -> list:
    if len(text) <= max_chars:
        return [text]

    chunks, current = [], ""
    for sentence in re.split(r'(?<=[।.!?])\s+', text):
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            while len(sentence) > max_chars:
                chunks.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            current = sentence

    if current:
        chunks.append(current)

    return chunks
