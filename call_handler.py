"""
call_handler.py
Manages active call sessions.
Exotel calls our webhook → we stream AI voice back.
Each call gets a ConversationManager instance.

FIX: get_opening_audio() was passing "hi-IN" (IETF tag) to synthesize_speech()
     which expected "hindi"/"hinglish". voice.py now handles both formats,
     but we also set the default correctly here.
"""
import time, re
from datetime import datetime
from typing import Dict
from agent import ConversationManager, get_opening_message
from voice import transcribe_audio, synthesize_speech
import sheets_manager as db

# In-memory store of active calls: call_sid → session data
active_calls: Dict[str, dict] = {}


def start_call_session(call_sid: str, caller_number: str, lead_id: str = None, direction: str = None) -> dict:
    """Initialize a new call session."""
    lead = None

    if lead_id:
        lead = db.get_lead_by_id(lead_id)
    elif caller_number:
        lead = db.get_lead_by_mobile(caller_number)

    if lead is None and caller_number:
        # Auto-create lead for inbound unknown callers
        source = direction or "inbound_call"
        new_id = db.add_lead({
            "mobile":  caller_number,
            "source":  source,
            "notes":   "Auto-created from inbound call",
        })
        lead    = db.get_lead_by_id(new_id)
        lead_id = new_id

    is_inbound = direction != "outbound" if direction else (lead_id is None or (lead and lead.get("source") == "inbound_call"))
    session = {
        "call_sid":    call_sid,
        "lead_id":     lead_id or (lead.get("lead_id") if lead else ""),
        "caller":      caller_number,
        "lead":        lead,
        "conversation": ConversationManager(lead, is_inbound=(direction != "outbound")),
        "start_time":  time.time(),
        "language":    "hinglish",
        "is_inbound":  is_inbound,
        "turn_count":  0,
        "silence_count": 0,
    }

    active_calls[call_sid] = session
    print(
        f"[CallHandler] Session started | SID: {call_sid} | "
        f"Lead: {lead_id} | Inbound: {is_inbound}"
    )
    return session


def get_opening_audio(call_sid: str) -> bytes:
    """
    Generate and return the opening greeting audio for this call.
    Called by main.py when Exotel requests /call/audio/opening/<call_sid>.
    """
    session = active_calls.get(call_sid)
    if not session:
        print(f"[CallHandler] get_opening_audio: no session for {call_sid}")
        return b""

    lead       = session.get("lead")
    is_inbound = session.get("is_inbound", False)

    opening_text = get_opening_message(lead, is_inbound=is_inbound)
    print(f"[CallHandler] Opening text: {opening_text[:120]}")

    session["conversation"].history.append({
        "role": "assistant", "content": opening_text
    })

    audio = synthesize_speech(opening_text, "hinglish")

    if not audio:
        print("[CallHandler] ⚠️  synthesize_speech returned empty bytes — check Sarvam API key/response")
    else:
        print(f"[CallHandler] ✅ Opening audio generated: {len(audio)} bytes")

    return audio


def process_customer_speech(call_sid: str, audio_bytes: bytes) -> bytes:
    """
    Core loop: audio in → STT → Groq → TTS → audio out.
    Returns audio bytes to play back to the customer.
    """
    session = active_calls.get(call_sid)
    if not session:
        return b""

    # 1. Speech to Text
    stt_result    = transcribe_audio(audio_bytes, "hi-IN")
    customer_text = stt_result.get("text", "").strip()
    detected_lang = stt_result.get("language", "hinglish")

    if not customer_text:
        silence_reply = "Ji? Kuch suna nahi — kya aap phir se bol sakte hain?"
        return synthesize_speech(silence_reply, session["language"])

    session["language"]  = detected_lang
    session["turn_count"] += 1

    print(f"[CallHandler] [{call_sid}] Customer: {customer_text}")

    # 2. Get AI response
    conv     = session["conversation"]
    ai_reply = conv.chat(customer_text)

    # Strip any JSON analysis block — never speak JSON to the customer
    voice_text = re.sub(r'\{[\s\S]*?\}', '', ai_reply).strip()

    print(f"[CallHandler] [{call_sid}] Priya: {voice_text[:120]}")

    # 3. Text to Speech
    audio_out = synthesize_speech(voice_text, detected_lang)
    return audio_out


def end_call_session(call_sid: str, duration_sec: int = 0) -> dict:
    """
    Called when Exotel sends the call-ended webhook.
    Analyses the conversation and updates the lead record.
    """
    session = active_calls.pop(call_sid, None)
    if not session:
        return {}

    from lead_manager import process_call_result

    conv       = session["conversation"]
    transcript = conv.get_full_transcript()
    analysis   = conv.analyze_call()
    lead_id    = session.get("lead_id", "")
    actual_dur = int(time.time() - session["start_time"]) if not duration_sec else duration_sec

    print(
        f"[CallHandler] Call ended | SID: {call_sid} | Duration: {actual_dur}s | "
        f"Temp: {analysis.get('temperature','?')} | Outcome: {analysis.get('call_outcome','?')}"
    )

    process_call_result(
        lead_id=lead_id,
        analysis=analysis,
        transcript=transcript,
        duration_sec=actual_dur,
        direction="inbound" if session.get("is_inbound") else "outbound",
    )

    # Save transcript to lead for next call memory
    if lead_id:
        lead = db.get_lead_by_id(lead_id)
        old_transcript = lead.get("last_transcript", "") if lead else ""
        call_num = int(lead.get("call_count", 0)) if lead else 1
        timestamp = datetime.now().strftime("%d %b %H:%M")
        new_entry = f"[Call {call_num} - {timestamp}]\n{transcript}"
        combined = f"{old_transcript}\n\n{new_entry}".strip() if old_transcript else new_entry
        db.update_lead(lead_id, {"last_transcript": combined[-3000:]})

    return analysis