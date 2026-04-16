"""
config.py — Central configuration loader with validation.

OPTIMIZATIONS:
- Added GROQ_FAST_MODEL for hybrid model routing (small/fast queries)
- Added GROQ_SMART_MODEL for complex queries
- Added latency-related configuration constants
- Added streaming and performance tuning knobs
"""
import os
import json
import logging

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("shubham-ai")

# -- Exotel telephony ---------------------------------------------------------
EXOTEL_API_KEY      = os.getenv("EXOTEL_API_KEY", "").strip()
EXOTEL_API_TOKEN    = os.getenv("EXOTEL_API_TOKEN", "").strip()
EXOTEL_ACCOUNT_SID  = os.getenv("EXOTEL_ACCOUNT_SID", "shubhammotors1").strip()
EXOTEL_PHONE_NUMBER = os.getenv("EXOTEL_PHONE_NUMBER", "+919513886363").strip()
EXOTEL_SUBDOMAIN    = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com").strip()
EXOTEL_APP_ID       = os.getenv("EXOTEL_APP_ID", "1186396")

# -- AI / ML APIs -------------------------------------------------------------
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "").strip()

# 🔥 OPTIMIZATION: Hybrid model routing — fast model for simple queries, smart model for complex
GROQ_FAST_MODEL     = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant").strip()
GROQ_SMART_MODEL    = os.getenv("GROQ_SMART_MODEL", "llama-3.3-70b-versatile").strip()
# Keep original for backward compatibility
GROQ_MODEL          = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

DEEPGRAM_API_KEY    = os.getenv("DEEPGRAM_API_KEY", "").strip()
SARVAM_API_KEY      = os.getenv("SARVAM_API_KEY", "").strip()
# [+] CHANGE: ngrok-specific token removed from active configuration surface.
# [-] Removed: NGROK_AUTH_TOKEN

# -- Google Sheets (optional) -------------------------------------------------
GOOGLE_SHEET_ID     = os.getenv("GOOGLE_SHEET_ID", "").strip()
try:
    GOOGLE_CREDENTIALS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))
except Exception:
    GOOGLE_CREDENTIALS = {}

# -- Business info ------------------------------------------------------------
BUSINESS_NAME       = os.getenv("BUSINESS_NAME", "Shubham Motors").strip()
BUSINESS_CITY       = os.getenv("BUSINESS_CITY", "Jaipur").strip()
WEBSITE_URL         = os.getenv("WEBSITE_URL", "").strip()
WORKING_HOURS_START = int(os.getenv("WORKING_HOURS_START", "9"))
WORKING_HOURS_END   = int(os.getenv("WORKING_HOURS_END", "19"))
WORKING_DAYS        = [
    d.strip() for d in os.getenv(
        "WORKING_DAYS",
        "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
    ).split(",")
    if d.strip()
]

# -- Sales team ---------------------------------------------------------------
SALES_TEAM = []
for _i in range(1, 6):
    _n = (os.getenv(f"SALESPERSON_{_i}_NAME") or "").strip()
    _m = (os.getenv(f"SALESPERSON_{_i}_MOBILE") or "").strip()
    if _n and _m:
        SALES_TEAM.append({"name": _n, "mobile": _m})

# -- Call settings ------------------------------------------------------------
MAX_FOLLOWUP_ATTEMPTS   = int(os.getenv("MAX_FOLLOWUP_ATTEMPTS", "3"))
DEFAULT_FOLLOWUP_TIME   = os.getenv("DEFAULT_FOLLOWUP_TIME", "10:00").strip()
DEFAULT_LANGUAGE        = os.getenv("DEFAULT_LANGUAGE", "hinglish").strip()
SILENCE_TIMEOUT_SECONDS = int(os.getenv("SILENCE_TIMEOUT_SECONDS", "5"))
PUBLIC_URL              = os.getenv("PUBLIC_URL", "http://localhost:5000").strip()
PORT                    = int(os.getenv("PORT", "5000"))

# 🔥 OPTIMIZATION: Latency tuning constants
# Reduced timeouts to fail fast instead of hanging
STT_TIMEOUT_SEC         = float(os.getenv("STT_TIMEOUT_SEC", "6.0"))
LLM_TIMEOUT_SEC         = float(os.getenv("LLM_TIMEOUT_SEC", "5.0"))
TTS_TIMEOUT_SEC         = float(os.getenv("TTS_TIMEOUT_SEC", "5.0"))
RECORDING_DOWNLOAD_TIMEOUT = float(os.getenv("RECORDING_DOWNLOAD_TIMEOUT", "6.0"))

# 🔥 OPTIMIZATION: Max response tokens — keep AI responses short for phone calls
LLM_MAX_TOKENS_FAST     = int(os.getenv("LLM_MAX_TOKENS_FAST", "40"))
LLM_MAX_TOKENS_SMART    = int(os.getenv("LLM_MAX_TOKENS_SMART", "45"))

# 🔥 OPTIMIZATION: Thread pool size for async operations
THREAD_POOL_SIZE        = int(os.getenv("THREAD_POOL_SIZE", "16"))

# 🔥 OPTIMIZATION: WebSocket audio buffer threshold (bytes) — lower = faster response
WS_AUDIO_BUFFER_THRESHOLD = int(os.getenv("WS_AUDIO_BUFFER_THRESHOLD", "24000"))


# -- Startup validation -------------------------------------------------------
def validate_config() -> list:
    """Return a list of warnings about missing/invalid configuration."""
    warnings = []
    if not EXOTEL_API_KEY:
        warnings.append("EXOTEL_API_KEY is not set -- outbound calls will fail")
    if not EXOTEL_API_TOKEN:
        warnings.append("EXOTEL_API_TOKEN is not set -- outbound calls will fail")
    if not GROQ_API_KEY:
        warnings.append("GROQ_API_KEY is not set -- AI conversations will fail")
    if not SARVAM_API_KEY:
        warnings.append("SARVAM_API_KEY is not set -- TTS/STT will fall back to Deepgram only")
    if not DEEPGRAM_API_KEY:
        warnings.append("DEEPGRAM_API_KEY is not set -- STT fallback unavailable")
    if PUBLIC_URL == "http://localhost:5000":
        # [+] CHANGE: generic production guidance (no ngrok assumption).
        warnings.append("PUBLIC_URL is localhost -- Exotel webhooks require a public HTTPS URL")
    if not SALES_TEAM:
        warnings.append("No salesperson configured -- hot lead assignment disabled")
    return warnings
