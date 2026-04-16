"""Parallel Groq call handler implementation.

Design goals:
- No breaking change to current handlers.
- Preserve Exotel XML shape (<Response><Play|Say/><Record .../></Response>).
- Replace STT + LLM with Groq-backed implementations.
- Resolve callback URLs at runtime from incoming request context.
"""
from __future__ import annotations

import re
import time
import tempfile
from pathlib import Path
from typing import Dict, Optional

from fastapi import Request

import sheets_manager as db
from agent import get_opening_message
from groq_client import generate_ai_response_groq, speech_to_text_groq
from voice_groq import text_to_speech_groq_pipeline

active_calls_groq: Dict[str, dict] = {}


def _xml_safe(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def resolve_public_base_url(request: Request) -> str:
    """Resolve public URL using request headers/runtime info, not config.PUBLIC_URL."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme or "https"
        return f"{scheme}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def record_xml(call_sid: str, action_url: str, play_url: Optional[str] = None, say_text: Optional[str] = None) -> str:
    content = ""
    if play_url:
        content = f"<Play>{play_url}</Play>"
    elif say_text:
        content = f'<Say language="hi-IN" voice="woman">{_xml_safe(say_text)}</Say>'

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
{content}
<Record action="{action_url}"
        method="POST"
        maxLength="60"
        timeout="10"
        playBeep="false"
        finishOnKey="#" />
</Response>"""


def start_call_session_groq(call_sid: str, caller_number: str, lead_id: str = "", direction: str = "inbound") -> dict:
    lead = db.get_lead_by_id(lead_id) if lead_id else db.get_lead_by_mobile(caller_number)
    if lead is None and caller_number:
        new_id = db.add_lead({
            "mobile": caller_number,
            "source": direction or "inbound_call",
            "notes": "Auto-created from Groq inbound call",
        })
        lead = db.get_lead_by_id(new_id)
        lead_id = new_id

    session = {
        "call_sid": call_sid,
        "lead_id": lead_id or (lead.get("lead_id") if lead else ""),
        "caller": caller_number,
        "lead": lead,
        "start_time": time.time(),
        "turn_count": 0,
        "silence_count": 0,
        "language": "hi",
    }
    active_calls_groq[call_sid] = session
    return session


def opening_audio_groq(call_sid: str) -> bytes:
    session = active_calls_groq.get(call_sid)
    if not session:
        return b""
    opening_text = get_opening_message(session.get("lead"), is_inbound=True)
    return text_to_speech_groq_pipeline(opening_text, "hinglish")


def process_customer_speech_groq(call_sid: str, audio_bytes: bytes) -> bytes:
    session = active_calls_groq.get(call_sid)
    if not session:
        return b""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        stt = speech_to_text_groq(tmp_path)
        customer_text = (stt.get("text") or "").strip()
        if not customer_text:
            session["silence_count"] += 1
            return text_to_speech_groq_pipeline(
                "Ji? Kuch suna nahi — kya aap phir se bol sakte hain?",
                "hinglish",
            )

        session["turn_count"] += 1
        session["language"] = stt.get("language") or session.get("language", "hi")

        ai_reply = generate_ai_response_groq(customer_text)
        voice_text = re.sub(r"\{[\s\S]*?\}", "", ai_reply).strip() or "Ji, samajh gayi. Kripya dubara batayein."
        return text_to_speech_groq_pipeline(voice_text, "hinglish")
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def build_initial_exotel_response(request: Request, call_sid: str, opening_audio_path: Optional[str] = None) -> str:
    base_url = resolve_public_base_url(request)
    action_url = f"{base_url}/call/gather/{call_sid}"
    if opening_audio_path:
        play_url = f"{base_url}/{opening_audio_path.lstrip('/')}"
        return record_xml(call_sid, action_url=action_url, play_url=play_url)

    fallback = (
        "Namaste! Main Priya bol rahi hoon, Shubham Motors Hero MotoCorp se, Jaipur. "
        "Aapki kaise madad kar sakti hoon?"
    )
    return record_xml(call_sid, action_url=action_url, say_text=fallback)
