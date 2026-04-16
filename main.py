"""
main.py — Shubham Motors AI Voice Agent
FastAPI server handling all Exotel webhooks, admin dashboard, lead import, offer upload.
Run: python main.py

KEY DESIGN NOTES:
- Exotel webhooks must respond within ~8-10 seconds or the call drops.
- All TTS/STT/AI calls are blocking HTTP — run them in a ThreadPoolExecutor.
- Exotel ExoML uses <Record> (NOT <Gather input="speech"> which is Twilio TwiML)
  <Record> captures customer audio → Exotel POSTs RecordingUrl → we download + STT.
- Always have a <Say> fallback in case Sarvam TTS is slow/unavailable.
"""
import base64
import os, json, re, io, asyncio, time
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import WebSocket, WebSocketDisconnect

import pandas as pd
import requests as _requests
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, Response
import uvicorn
import numpy as np 

import config
import sheets_manager as db
from call_handler import (
    start_call_session, get_opening_audio,
    end_call_session, active_calls
)
from agent import get_opening_message
from lead_manager import process_call_result, add_leads_from_import, get_dashboard_stats
from exotel_client import make_outbound_call
from scraper import parse_offer_file, scrape_hero_website
from scheduler import start_scheduler, stop_scheduler
from voice import synthesize_speech, transcribe_audio, synthesize_speech_async, transcribe_audio_async
from keep_alive import keep_alive
from audio_utils import _mp3_to_pcm, _pcm_to_wav, _is_silence

# ── STARTUP / SHUTDOWN ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    keep_alive()
    print(f"\n{'='*60}")
    print(f"  SHUBHAM MOTORS AI AGENT — STARTING UP")
    print(f"  {config.BUSINESS_NAME}, {config.BUSINESS_CITY}")
    print(f"  Public URL: {config.PUBLIC_URL}")
    print(f"  Exophone: {config.EXOTEL_PHONE_NUMBER}")
    print(f"{'='*60}\n")
    try:
        scrape_hero_website()
        print("Hero bike catalog loaded")
    except Exception as e:
        print(f"Catalog load failed: {e} (using fallback data)")
    start_scheduler()

    async def _prewarm():
        await asyncio.sleep(3)
        from agent import get_opening_message
        text = get_opening_message(None, is_inbound=True)
        audio = await _run(synthesize_speech, text, "hinglish", timeout=15.0)
        if audio:
            pcm = await _run(_mp3_to_pcm, audio, timeout=5.0)
            if pcm:
                _greeting_pcm_cache["data"] = pcm
                print(f"[Startup] ✅ Greeting PCM cached: {len(pcm)} bytes")
        else:
            print("[Startup] ⚠️ Greeting prewarm failed")

    asyncio.create_task(_prewarm())

    async def _build_phrase_cache():
        await asyncio.sleep(8)  # let greeting prewarm finish first
        from phrase_cache import build_cache
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        await loop.run_in_executor(_executor, build_cache)

    asyncio.create_task(_build_phrase_cache())

    yield

    print("\n[Shutdown] Stopping scheduler...")
    stop_scheduler()
    print("[Shutdown] Done")


# ── App setup ──────────────────────────────────────────────────────────────────
app = FastAPI(title="Shubham Motors AI Agent", version="2.1.0", lifespan=lifespan)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
_greeting_pcm_cache = {}
from state import _pending_outbound

# Thread pool for ALL blocking I/O (Sarvam TTS, Deepgram STT, Groq LLM)
# This prevents blocking the FastAPI async event loop
_executor = ThreadPoolExecutor(max_workers=12)


# ── HEALTH ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return JSONResponse({
        "status": "running",
        "agent": "Shubham Motors AI Voice Agent",
        "dashboard": f"{config.PUBLIC_URL}/dashboard",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── HELPER FUNCTIONS ───────────────────────────────────────────────────────────

def _hangup_xml() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'


def _xml_safe(text: str) -> str:
    """Escape XML special characters for use inside <Say> tags."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _record_xml(call_sid: str, play_url: str = None, say_text: str = None) -> str:
    """
    Return ExoML that plays audio then records customer's reply.
    Uses Sarvam TTS audio via <Play>.
    """

    content = ""
    if play_url:
        content = f"<Play>{play_url}</Play>"
    elif say_text:
        content = f'<Say language="hi-IN" voice="woman">{_xml_safe(say_text)}</Say>'

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
{content}
<Record action="{config.PUBLIC_URL}/call/gather/{call_sid}"
        method="POST"
        maxLength="60"
        timeout="10"
        playBeep="false"
        finishOnKey="#" />
</Response>"""


# [+] CHANGE: resolve public base URL from runtime request context (no static-only dependency).
def _resolve_public_base_url(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme or "https"
        return f"{scheme}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


# [+] CHANGE: request-aware XML builder using runtime-resolved base URL.
def _record_xml_runtime(request: Request, call_sid: str, play_url: str = None, say_text: str = None) -> str:
    content = ""
    if play_url:
        content = f"<Play>{play_url}</Play>"
    elif say_text:
        content = f'<Say language="hi-IN" voice="woman">{_xml_safe(say_text)}</Say>'
    action_url = f"{_resolve_public_base_url(request)}/call/gather/{call_sid}"
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

def _download_recording(url: str) -> bytes:
    """
    Download a <Record> audio file from Exotel.
    Exotel requires API key+token authentication for recording URLs.
    """
    try:
        r = _requests.get(
            url,
            auth=(config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN),
            timeout=15
        )
        r.raise_for_status()
        print(f"[Audio] Downloaded {len(r.content)} bytes from Exotel recording")
        return r.content
    except Exception as e:
        print(f"[Audio] Download failed: {e}")
        return b""


async def _run(fn, *args, timeout: float = 12.0):
    """
    Run a blocking function in the thread pool with a timeout.
    Returns None if timeout or exception occurs.
    Essential for keeping Exotel webhook response time under ~8s.
    """
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, fn, *args),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        print(f"[Async] Timeout ({timeout}s) in {getattr(fn, '__name__', str(fn))}")
        return None
    except Exception as e:
        print(f"[Async] Error in {getattr(fn, '__name__', str(fn))}: {e}")
        return None


# ── EXOTEL WEBHOOKS ────────────────────────────────────────────────────────────

@app.api_route("/call/incoming", methods=["GET", "POST"])
async def incoming_call(request: Request, background_tasks: BackgroundTasks):
    if request.method == "GET":
        data = request.query_params
    else:
        data = await request.form()

    call_sid = data.get("CallSid", "").strip()
    caller   = data.get("From", "").strip()

    print(f"\n[Incoming] Call from {caller} | SID: {call_sid}")

    if not call_sid:
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml"
        )

    start_call_session(call_sid, caller, direction="inbound")

    greeting = "Namaste! Main Priya bol rahi hoon, Shubham Motors Hero MotoCorp se, Jaipur. Aap ka call receive karke bahut khushi hui! Kaise madad kar sakti hoon aapki?"
    # [+] CHANGE: use runtime URL resolution for callback action URL.
    xml = _record_xml_runtime(request, call_sid, say_text=greeting)

    return Response(content=xml, media_type="application/xml")

@app.api_route("/call/handler", methods=["GET", "POST"])
async def outbound_call_handler(request: Request):
    """
    Exotel hits this when our outbound call connects to the customer.
    Same flow as incoming — greet + record.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "").strip()
    called   = form.get("To", "").strip()
    lead_id  = form.get("CustomField", "").strip()

    print(f"\n[Outbound] Call to {called} | SID: {call_sid} | Lead: {lead_id}")

    if not call_sid:
        return Response(content=_hangup_xml(), media_type="application/xml")

    # Avoid duplicate sessions (outbound can sometimes trigger twice)
    if call_sid not in active_calls:
        start_call_session(call_sid, called, lead_id=lead_id, direction="outbound")
    else:
        print(f"[Outbound] Session already exists for {call_sid}, skipping duplicate init")

    opening_url = None
    try:
        opening_audio = await _run(get_opening_audio, call_sid, timeout=8.0)
        if opening_audio:
            opening_path = UPLOAD_DIR / f"opening_{call_sid}.mp3"
            opening_path.write_bytes(opening_audio)
            # [+] CHANGE: runtime-resolved URL for Exotel playable media.
            opening_url = f"{_resolve_public_base_url(request)}/call/audio/opening/{call_sid}"
    except Exception as e:
        print(f"[Outbound] Greeting gen error: {e}")

    if opening_url:
            return Response(
            content=_record_xml_runtime(request, call_sid, play_url=opening_url),
            media_type="application/xml"
        )
    else:
        greeting = (
            "Namaste! Main Priya bol rahi hoon, Shubham Motors Hero MotoCorp se, Jaipur. "
            "Aapki Hero bike enquiry ke baare mein baat karna tha — "
            "kya aap abhi thodi der baat kar sakte hain?"
        )
        return Response(
            content=_record_xml_runtime(request, call_sid, say_text=greeting),
            media_type="application/xml"
        )


@app.post("/call/gather/{call_sid}")
async def handle_gather(call_sid: str, request: Request):
    """
    Exotel POSTs here after <Record> captures customer's speech.

    For <Record> responses: form contains RecordingUrl (audio file URL)
    For <Gather> responses: form contains SpeechResult or Digits

    We download the recording → STT (Deepgram/Sarvam) → Groq LLM → TTS → return ExoML.
    All heavy operations run async in ThreadPoolExecutor with timeouts.
    """
    try:
        form = await request.form()

        # <Record> sends RecordingUrl; <Gather> sends SpeechResult/Digits
        recording_url = form.get("RecordingUrl", "").strip()
        speech_result = form.get("SpeechResult", "").strip()
        digits = form.get("Digits", "").strip()

        print(
            f"[Gather] [{call_sid}] RecordingUrl={bool(recording_url)} "
            f"SpeechResult='{speech_result[:60]}' Digits='{digits}'"
        )

        # ── Get active session ─────────────────────────────────────────
        session = active_calls.get(call_sid)
        if not session:
            print(f"[Gather] [{call_sid}] No session found — hanging up")
            return Response(content=_hangup_xml(), media_type="application/xml")

        # ── Transcribe customer input ──────────────────────────────────
        customer_input = speech_result or digits

        if not customer_input and recording_url:
            # Download recording from Exotel (requires auth)
            audio_bytes = await _run(_download_recording, recording_url, timeout=12.0)

            if audio_bytes:
                # Transcribe with Sarvam/Deepgram
                stt_result = await _run(transcribe_audio, audio_bytes, "hi-IN", timeout=10.0)
                if stt_result:
                    customer_input = stt_result.get("text", "").strip()
                    detected_lang = stt_result.get("language", "hinglish")

                    print(
                        f"[Gather] [{call_sid}] STT: '{customer_input[:120]}' "
                        f"({detected_lang})"
                    )

                    if customer_input:
                        session["language"] = detected_lang

        # ── Handle silence / empty input ───────────────────────────────
        if not customer_input:
            silence_count = session.get("silence_count", 0) + 1
            session["silence_count"] = silence_count

            print(f"[Gather] [{call_sid}] Silence #{silence_count}")

            if silence_count >= 3:
                print(f"[Gather] [{call_sid}] 3 silences — hanging up")
                return Response(content=_hangup_xml(), media_type="application/xml")

            retry_text = "Ji? Kuch clearly suna nahi — kya aap thoda louder bol sakte hain?"

            return Response(
                content=_record_xml_runtime(request, call_sid, say_text=retry_text),
                media_type="application/xml",
            )

        # Reset silence counter on successful speech
        session["silence_count"] = 0
        session["turn_count"] = session.get("turn_count", 0) + 1

        print(
            f"[Gather] [{call_sid}] Customer (turn {session['turn_count']}): "
            f"'{customer_input[:120]}'"
        )

        # ── Get AI response via Groq LLM ───────────────────────────────
        from intent import detect_intent
        conv = session["conversation"]
        voice_text = None


        intent_response = detect_intent(customer_input, lead=session.get("lead"))
        if intent_response:
            voice_text = intent_response
            conv.history.append({"role": "user", "content": customer_input})
            conv.history.append({"role": "assistant", "content": voice_text})
            print(f"[Gather] [{call_sid}] Intent matched — skipping Groq")
        else:
            ai_reply = await _run(conv.chat, customer_input, timeout=15.0)
            if ai_reply:
                voice_text = re.sub(r"\{[\s\S]*?\}", "", ai_reply).strip()
            if not voice_text:
                voice_text = "Ji, main samajh rahi hoon. Kya aap thoda aur detail de sakte hain?"

        print(f"[Gather] [{call_sid}] Priya: {voice_text[:120]}")

        # ── Detect language for TTS routing ────────────────────────────
        devanagari_count = sum(1 for c in customer_input if "\u0900" <= c <= "\u097F")

        if devanagari_count > len(customer_input) * 0.3:
            lang = "hindi"
        else:
            lang = session.get("language", "hinglish")

        session["language"] = lang

        # ── Generate TTS audio ─────────────────────────────────────────
        audio_url = None

        from phrase_cache import get_cached_audio
        cached_pcm = get_cached_audio(voice_text)
        if cached_pcm:
            print(f"[PhraseCache] Gather: serving cached audio ({len(cached_pcm)} bytes)")
            audio_path = UPLOAD_DIR / f"response_{call_sid}.wav"
            audio_path.write_bytes(cached_pcm)
            audio_url = f"{_resolve_public_base_url(request)}/call/audio/response/{call_sid}"
        else:
            ai_audio = await _run(synthesize_speech, voice_text, lang, timeout=12.0)
            if ai_audio:
                audio_path = UPLOAD_DIR / f"response_{call_sid}.mp3"
                audio_path.write_bytes(ai_audio)
                audio_url = f"{_resolve_public_base_url(request)}/call/audio/response/{call_sid}"

        # ── Return response to Exotel ──────────────────────────────────
        if audio_url:
                return Response(
                content=_record_xml_runtime(request, call_sid, play_url=audio_url),
                media_type="application/xml",
            )
        else:
            print(f"[Gather] [{call_sid}] TTS unavailable — using Say fallback")

            return Response(
                content=_record_xml_runtime(request, call_sid, say_text=voice_text),
                media_type="application/xml",
            )

    except Exception as e:
        print(f"[Gather ERROR] {e}")

        return Response(
            content=_record_xml_runtime(request, call_sid, say_text="Sorry, ek technical issue ho gaya."),
            media_type="application/xml",
        )


@app.post("/call/status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """
    Exotel hits this when call ends.
    Analyse conversation and update lead in background (don't block Exotel).
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    status   = form.get("Status", "")
    duration = int(form.get("Duration", 0))

    print(f"\n[Status] Call {call_sid} ended | Status: {status} | Duration: {duration}s")

    # Process in background — don't make Exotel wait
    background_tasks.add_task(end_call_session, call_sid, duration)

    # Cleanup audio files
    for prefix in ["opening", "response", "retry"]:
        f = UPLOAD_DIR / f"{prefix}_{call_sid}.mp3"
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass

    return JSONResponse({"received": True})


# ── AUDIO FILE SERVING ─────────────────────────────────────────────────────────

@app.get("/call/audio/opening/{call_sid}")
async def serve_opening_audio(call_sid: str):
    print(f"[Audio] Opening requested for {call_sid}")
    print(f"[Audio] Files in uploads: {list(UPLOAD_DIR.iterdir())}")

    # Serve call-specific pre-generated file
    path = UPLOAD_DIR / f"opening_{call_sid}.mp3"
    if path.exists():
        print(f"[Audio] ✅ Serving pre-generated file")
        return Response(content=path.read_bytes(), media_type="audio/mpeg")

    # Fallback to warmup file (same greeting, generated at startup)
    warmup = UPLOAD_DIR / "opening_warmup.mp3"
    if warmup.exists():
        print(f"[Audio] ✅ Serving warmup file")
        return Response(content=warmup.read_bytes(), media_type="audio/mpeg")

    # Last resort: generate on-demand
    audio = await _run(get_opening_audio, call_sid, timeout=10.0)
    if not audio:
        print("[Audio] ❌ No audio returned")
        return Response(status_code=404)

    return Response(content=audio, media_type="audio/mpeg")

@app.get("/call/audio/response/{call_sid}")
async def serve_response_audio(call_sid: str):
    for ext in ["mp3", "wav"]:
        path = UPLOAD_DIR / f"response_{call_sid}.{ext}"
        if path.exists():
            media_type = "audio/mpeg" if ext == "mp3" else "audio/wav"
            return Response(content=path.read_bytes(), media_type=media_type)
    return Response(status_code=404)

# ── ADMIN API ──────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = get_dashboard_stats()
    leads = db.get_all_leads()
    priority = {"hot": 0, "warm": 1, "new": 2, "active": 3, "cold": 4, "dead": 5, "converted": 6}
    leads.sort(key=lambda x: priority.get(x.get("status", "new"), 9))
    return HTMLResponse(_render_dashboard(stats, leads[:100]))


@app.get("/api/leads")
async def api_leads():
    return JSONResponse(db.get_all_leads())


@app.post("/api/leads/add")
async def api_add_lead(request: Request):
    data = await request.json()
    lead_id = db.add_lead(data)
    return JSONResponse({"success": True, "lead_id": lead_id})


@app.post("/api/leads/import")
async def import_leads(file: UploadFile = File(...)):
    content = await file.read()
    ext = Path(file.filename).suffix.lower()
    try:
        df = pd.read_csv(io.BytesIO(content)) if ext == ".csv" else pd.read_excel(io.BytesIO(content))
        df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]
        col_map = {
            "phone": "mobile", "contact": "mobile", "number": "mobile",
            "customer_name": "name", "customer": "name",
            "model": "interested_model", "bike": "interested_model",
        }
        df.rename(columns=col_map, inplace=True)
        leads = df.to_dict(orient="records")
        ids = add_leads_from_import(leads)
        return JSONResponse({"success": True, "imported": len(ids), "skipped": len(leads) - len(ids)})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/call/make")
async def trigger_call(request: Request, background_tasks: BackgroundTasks):
    data    = await request.json()
    lead_id = data.get("lead_id", "")
    mobile  = data.get("mobile", "")
    if not mobile and lead_id:
        lead = db.get_lead_by_id(lead_id)
        if lead:
            mobile = lead.get("mobile", "")
    if not mobile:
        raise HTTPException(status_code=400, detail="Mobile number required")
    _pending_outbound.add(mobile.lstrip("0"))
    background_tasks.add_task(make_outbound_call, mobile, lead_id)
    return JSONResponse({"success": True, "message": f"Calling {mobile}..."})


@app.post("/api/offers/upload")
async def upload_offer(
    file: UploadFile = File(...),
    title: str = Form(...),
    valid_till: str = Form(""),
    models: str = Form(""),
):
    content  = await file.read()
    filepath = UPLOAD_DIR / file.filename
    filepath.write_bytes(content)
    offer_text = parse_offer_file(str(filepath))
    offer_id   = db.add_offer({
        "title": title,
        "description": offer_text[:2000],
        "valid_till": valid_till,
        "models": models,
    })
    return JSONResponse({"success": True, "offer_id": offer_id, "preview": offer_text[:200]})


@app.get("/api/stats")
async def api_stats():
    from sheets_manager import get_call_stats
    stats = get_dashboard_stats()
    call_stats = get_call_stats()
    return JSONResponse({**stats, **call_stats})


@app.get("/api/active-calls")
async def api_active_calls():
    return JSONResponse({
        "active_calls": len(active_calls),
        "call_sids": list(active_calls.keys())
    })

async def _process_speech(buf: bytes, call_sid: str, stream_sid: str, websocket: WebSocket, state: dict):
    session = active_calls.get(call_sid)
    if not session:
        return
    if len(buf) < 3200:
        print(f"[Voicebot] Buffer too small ({len(buf)} bytes), skipping")
        return
    if _is_silence(buf):
        print("[Voicebot] Silence detected, skipping STT")
        return
    try:
        wav_bytes = _pcm_to_wav(buf)
        if not wav_bytes:
            print("[Voicebot] Audio conversion failed")
            return
        stt_result = await transcribe_audio_async(wav_bytes, "hi-IN")
        customer_text = stt_result.get("text", "").strip() if stt_result else ""
        print(f"[Voicebot] STT: '{customer_text[:120]}'")

        if not customer_text:
            return

        detected_lang = stt_result.get("language", "hinglish")
        session["language"] = detected_lang

        from intent import detect_intent
        conv = session["conversation"]
        
        # Try intent detection first — skip Groq if matched
        intent_response = detect_intent(customer_text, lead=session.get("lead"))
        if intent_response:
            voice_text = intent_response
            # Still add to conversation history for context
            conv.history.append({"role": "user", "content": customer_text})
            conv.history.append({"role": "assistant", "content": voice_text})
        else:
            ai_reply = await _run(conv.chat, customer_text, timeout=25.0)
            voice_text = re.sub(r"\{.*", "", ai_reply, flags=re.DOTALL).strip() if ai_reply else ""
        
            if not voice_text:
                voice_text = "Ji, main samajh rahi hoon. Kya aap thoda aur detail de sakte hain?"

        print(f"[Voicebot] Priya: {voice_text[:120]}")

        from phrase_cache import get_cached_audio
        pcm = get_cached_audio(voice_text)
        if pcm:
            print(f"[PhraseCache] Serving cached audio ({len(pcm)} bytes)")
        else:
            audio = await synthesize_speech_async(voice_text, detected_lang)
            if audio:
                pcm = await _run(_mp3_to_pcm, audio, timeout=5.0)
        if pcm:
            b64 = base64.b64encode(pcm).decode("ascii")
            await websocket.send_text(json.dumps({
                "event": "media",
                "stream_sid": stream_sid,
                "media": {"payload": b64}
            }))
            print(f"[Voicebot] Sent response ({len(pcm)} bytes)")
            response_secs = len(pcm) / 16000  # 8kHz 16-bit = 16000 bytes/sec
            state["listen_after"] = time.monotonic() + response_secs + 0.8
            print(f"[Voicebot] Blocking input for {response_secs:.1f}s")

    except Exception as e:
        print(f"[Voicebot] _process_speech error: {e}")

# ── VOICEBOT WEBSOCKET ─────────────────────────────────────────────────────────

@app.websocket("/call/stream")
async def voicebot_stream(websocket: WebSocket):
    await websocket.accept()
    print("[Voicebot] WebSocket connected")

    call_sid = None
    stream_sid = ""
    audio_buffer = b""
    state = {"listen_after": 0.0}
    _busy = [False]

    try:
        async for message in websocket.iter_text():
            data = json.loads(message)
            event = data.get("event", "")

            if event == "connected":
                print("[Voicebot] Stream connected")

            elif event == "start":
                start_data = data.get("start", {})
                call_sid = start_data.get("callSid") or start_data.get("call_sid") or ""
                stream_sid = start_data.get("streamSid") or start_data.get("stream_sid") or ""
                caller = start_data.get("from", "")
                called = start_data.get("to", "")
                print(f"[Voicebot] Call started | SID: {call_sid} | From: {caller} | To: {called}")
                print(f"[Voicebot] Raw start_data: {start_data}")

                called_stripped = called.lstrip("0")
                caller_stripped = caller.lstrip("0")

                if called_stripped in _pending_outbound:
                    direction = "outbound"
                    mobile = called
                    _pending_outbound.discard(called_stripped)
                elif caller_stripped in _pending_outbound:
                    direction = "outbound"
                    mobile = caller
                    _pending_outbound.discard(caller_stripped)
                else:
                    direction = "inbound"
                    mobile = caller

                print(f"[Voicebot] Direction: {direction} | Customer mobile: {mobile}")

                start_call_session(call_sid, caller, direction=direction)
                session = active_calls.get(call_sid)

                if session:
                    greeting = get_opening_message(session.get("lead"), is_inbound=True)
                    session["conversation"].history.append({
                        "role": "assistant", "content": greeting
                    })

                    pcm = _greeting_pcm_cache.get("data")
                    if not pcm:
                        audio = await _run(synthesize_speech, greeting, "hinglish", timeout=10.0)
                        if audio:
                            pcm = await _run(_mp3_to_pcm, audio, timeout=5.0)

                    if pcm:
                        b64 = base64.b64encode(pcm).decode("ascii")
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "stream_sid": stream_sid,
                            "media": {"payload": b64}
                        }))
                        greeting_secs = len(pcm) / 16000
                        state["listen_after"] = time.monotonic() + greeting_secs + 1.0
                        print(f"[Voicebot] Sent greeting ({len(pcm)} bytes), blocking {greeting_secs:.1f}s")
                        await websocket.send_text(json.dumps({
                            "event": "mark",
                            "stream_sid": stream_sid,
                            "mark": {"name": "greeting_done"}
                        }))

            elif event == "media":
                if _busy[0]:
                    continue
                if time.monotonic() < state["listen_after"]:
                    continue
                payload = data.get("media", {}).get("payload", "")
                if not payload:
                    continue

                chunk = base64.b64decode(payload)
                audio_buffer += chunk

                if len(audio_buffer) >= config.WS_AUDIO_BUFFER_THRESHOLD and not _busy[0]:
                    buf = audio_buffer
                    audio_buffer = b""
                    _busy[0] = True

                    async def handle_speech(b=buf):
                        try:
                            await _process_speech(b, call_sid, stream_sid, websocket, state)
                        finally:
                            _busy[0] = False

                    asyncio.create_task(handle_speech())

            elif event == "stop":
                print(f"[Voicebot] Stream stopped | SID: {call_sid}")
                if call_sid:
                    end_call_session(call_sid, 0)

            elif event == "mark":
                name = data.get('mark', {}).get('name', '')
                print(f"[Voicebot] Mark received: {name}")

    except WebSocketDisconnect:
        print(f"[Voicebot] Disconnected | SID: {call_sid}")
        if call_sid:
            end_call_session(call_sid, 0)
    except Exception as e:
        print(f"[Voicebot] Error: {e}")
        if call_sid:
            end_call_session(call_sid, 0)

# ── AUDIO CONVERSION HELPERS ───────────────────────────────────────────────────

def _encode_pcm(pcm_bytes: bytes) -> str:
    """Base64 encode PCM bytes for Exotel WebSocket."""
    return base64.b64encode(pcm_bytes).decode("utf-8")

# ── DASHBOARD HTML ─────────────────────────────────────────────────────────────

def _render_dashboard(stats: dict, leads: list) -> str:
    badge = {
        "hot": "🔥", "warm": "🟡", "cold": "❄️",
        "dead": "☠️", "converted": "✅", "new": "🆕", "active": "📞",
        "lost_to_codealer": "⚠️", "lost_to_competitor": "❌"
    }

    rows = ""
    for l in leads:
        s = l.get("status", "new")
        ic = badge.get(s, "⚪")
        rows += f"""
        <tr>
          <td>{ic} {l.get('name') or '—'}</td>
          <td>{l.get('mobile','')}</td>
          <td>{l.get('interested_model') or '—'}</td>
          <td>{l.get('budget') or '—'}</td>
          <td><span class="badge badge-{s}">{s.upper()}</span></td>
          <td>{l.get('assigned_to') or '—'}</td>
          <td>{l.get('last_called') or '—'}</td>
          <td>{l.get('next_followup') or '—'}</td>
          <td>{l.get('call_count', 0)}</td>
          <td>
            <button onclick="callLead('{l.get('lead_id','')}','{l.get('mobile','')}')"
                    class="btn-call">📞 Call</button>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shubham Motors — AI Agent</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', sans-serif; background: #f4f6f9; color: #1a1a2e; min-height: 100vh; }}

  /* ── HEADER ── */
  .header {{
    background: #fff;
    padding: 14px 30px;
    border-bottom: 3px solid #cc2200;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .header-left {{ display: flex; align-items: center; gap: 14px; }}
  .header-logo {{
    background: #ea9999;
    color: #fff;
    width: 42px; height: 42px;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.4em;
  }}
  .header-title {{ font-size: 1.2em; font-weight: 700; color: #1a1a2e; }}
  .header-sub {{ font-size: 0.75em; color: #888; margin-top: 2px; }}
  .header-right {{ display: flex; align-items: center; gap: 10px; }}
  .live-badge {{
    background: #e8f5e9; color: #2e7d32;
    padding: 5px 12px; border-radius: 20px;
    font-size: 0.78em; font-weight: 600;
    border: 1px solid #a5d6a7;
  }}
  .btn-refresh {{
    background: #f4f6f9; border: 1px solid #ddd;
    padding: 7px 14px; border-radius: 6px;
    cursor: pointer; font-size: 0.82em; color: #555;
  }}
  .btn-refresh:hover {{ background: #e8eaf0; }}

  /* ── STAT CARDS ── */
  .stats-row {{
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 14px;
    padding: 20px 30px;
  }}
  .stat-card {{
    background: #fff;
    border-radius: 12px;
    padding: 16px 18px;
    border: 1px solid #e8eaf0;
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
    position: relative;
    overflow: hidden;
  }}
  .stat-card::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent, #cc2200);
  }}
  .stat-icon {{
    font-size: 1.6em;
    margin-bottom: 8px;
  }}
  .stat-num {{
    font-size: 2em;
    font-weight: 700;
    color: var(--accent, #1a1a2e);
    line-height: 1;
  }}
  .stat-lbl {{
    font-size: 0.72em;
    color: #888;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .stat-sub {{
    font-size: 0.7em;
    color: #aaa;
    margin-top: 2px;
  }}

  /* ── TOOLBAR ── */
  .toolbar {{
    display: flex;
    gap: 8px;
    padding: 0 30px 16px;
    flex-wrap: wrap;
  }}
  .btn {{
    background: #cc2200; color: #fff;
    border: none; padding: 9px 16px;
    border-radius: 6px; cursor: pointer;
    font-size: 0.83em; font-weight: 600;
  }}
  .btn:hover {{ background: #aa1a00; }}
  .btn-green {{ background: #2e7d32; }}
  .btn-green:hover {{ background: #1b5e20; }}
  .btn-purple {{ background: #6a1b9a; }}
  .btn-purple:hover {{ background: #4a148c; }}
  .btn-teal {{ background: #00695c; }}
  .btn-teal:hover {{ background: #004d40; }}

  /* ── SECTION CARD ── */
  .section {{ padding: 0 30px 30px; }}
  .section-card {{
    background: #fff;
    border-radius: 12px;
    border: 1px solid #e8eaf0;
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
    overflow: hidden;
    margin-bottom: 20px;
  }}
  .section-header {{
    padding: 14px 20px;
    border-bottom: 1px solid #f0f0f0;
    font-weight: 600;
    font-size: 0.9em;
    color: #444;
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  /* ── TABLE ── */
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
  th {{
    background: #f8f9fb;
    color: #666;
    padding: 11px 14px;
    text-align: left;
    font-size: 0.78em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid #eee;
  }}
  td {{ padding: 11px 14px; border-bottom: 1px solid #f5f5f5; }}
  tr:hover {{ background: #fafbfc; }}
  .btn-call {{
    background: #1565c0; color: #fff;
    border: none; padding: 5px 11px;
    border-radius: 4px; cursor: pointer;
    font-size: 0.78em;
  }}
  .btn-call:hover {{ background: #0d47a1; }}

  /* ── BADGES ── */
  .badge {{
    padding: 3px 9px; border-radius: 20px;
    font-size: 0.72em; font-weight: 600;
  }}
  .badge-hot {{ background: #fdecea; color: #c62828; }}
  .badge-warm {{ background: #fff8e1; color: #f57f17; }}
  .badge-cold {{ background: #e3f2fd; color: #1565c0; }}
  .badge-dead {{ background: #f5f5f5; color: #9e9e9e; }}
  .badge-converted {{ background: #e8f5e9; color: #2e7d32; }}
  .badge-new {{ background: #e8eaf6; color: #3949ab; }}
  .badge-active {{ background: #e0f7fa; color: #00695c; }}
  .badge-lost_to_codealer {{ background: #fff3e0; color: #e65100; }}
  .badge-lost_to_competitor {{ background: #fce4ec; color: #880e4f; }}

  /* ── MODALS ── */
  .modal {{
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.5); z-index: 1000;
    align-items: center; justify-content: center;
  }}
  .modal.open {{ display: flex; }}
  .mbox {{
    background: #fff; border-radius: 12px;
    padding: 28px; width: 460px; max-width: 95vw;
    box-shadow: 0 8px 32px rgba(0,0,0,0.15);
  }}
  .mbox h3 {{ margin-bottom: 18px; color: #1a1a2e; }}
  label {{ color: #666; font-size: 0.82em; display: block; margin-bottom: 4px; }}
  input, select, textarea {{
    width: 100%; background: #f8f9fb;
    border: 1px solid #e0e0e0; color: #1a1a2e;
    padding: 9px 12px; border-radius: 6px;
    margin-bottom: 10px; font-size: 0.88em;
  }}
  input:focus, select:focus, textarea:focus {{
    outline: none; border-color: #cc2200;
  }}
  .row {{ display: flex; gap: 8px; }}
  .hint {{ color: #aaa; font-size: 0.78em; margin-bottom: 12px; }}

  /* ── TOAST ── */
  #toastContainer {{ position: fixed; bottom: 20px; right: 20px; z-index: 9999; }}
  .toast {{
    background: #2e7d32; color: #fff;
    padding: 12px 20px; border-radius: 8px;
    margin-top: 8px; font-size: 0.88em;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  }}
  .toast.err {{ background: #c62828; }}

  /* ── TABLE FILTERS ── */
  .table-filters {{
    display: flex; gap: 10px;
    padding: 14px 20px;
    border-bottom: 1px solid #f0f0f0;
    flex-wrap: wrap;
    align-items: center;
  }}
  .table-filters input, .table-filters select {{
    margin-bottom: 0;
    width: auto;
    min-width: 160px;
    font-size: 0.82em;
  }}
  .filter-label {{
    font-size: 0.78em;
    color: #888;
    font-weight: 600;
  }}
</style>
</head>
<body>

<!-- ── HEADER ── -->
<div class="header">
  <div class="header-left">
    <div class="header-logo">🏍️</div>
    <div>
      <div class="header-title">Shubham Motors AI Agent</div>
      <div class="header-sub">Hero MotoCorp Authorized Dealer • Lal Kothi, Jaipur</div>
    </div>
  </div>
  <div class="header-right">
    <span id="activeCalls" class="live-badge">🟢 Loading...</span>
    <button class="btn-refresh" onclick="location.reload()">🔄 Refresh</button>
  </div>
</div>

<!-- ── STAT CARDS ── -->
<div class="stats-row">
  <div class="stat-card" style="--accent:#cc2200">
    <div class="stat-icon">👥</div>
    <div class="stat-num" style="color:#cc2200">{stats.get('total',0)}</div>
    <div class="stat-lbl">Total Leads</div>
    <div class="stat-sub">{stats.get('new',0)} new today</div>
  </div>
  <div class="stat-card" style="--accent:#c62828">
    <div class="stat-icon">🔥</div>
    <div class="stat-num" style="color:#c62828">{stats.get('hot',0)}</div>
    <div class="stat-lbl">Hot Leads</div>
    <div class="stat-sub">Ready to buy</div>
  </div>
  <div class="stat-card" style="--accent:#2e7d32">
    <div class="stat-icon">✅</div>
    <div class="stat-num" style="color:#2e7d32">{stats.get('converted',0)}</div>
    <div class="stat-lbl">Converted</div>
    <div class="stat-sub">Bikes sold</div>
  </div>
  <div class="stat-card" style="--accent:#1565c0">
    <div class="stat-icon">📞</div>
    <div class="stat-num" style="color:#1565c0">{stats.get('calls_today',0)}</div>
    <div class="stat-lbl">Calls Today</div>
    <div class="stat-sub">{stats.get('total_calls',0)} total</div>
  </div>
  <div class="stat-card" style="--accent:#6a1b9a">
    <div class="stat-icon">⏱️</div>
    <div class="stat-num" style="color:#6a1b9a">{stats.get('avg_duration_min',0)}</div>
    <div class="stat-lbl">Avg Duration</div>
    <div class="stat-sub">minutes per call</div>
  </div>
  <div class="stat-card" style="--accent:#00695c">
    <div class="stat-icon">❄️</div>
    <div class="stat-num" style="color:#00695c">{stats.get('active',0)}</div>
    <div class="stat-lbl">Active Leads</div>
    <div class="stat-sub">{stats.get('warm',0)} warm • {stats.get('cold',0)} cold</div>
  </div>
</div>

<!-- ── TOOLBAR ── -->
<div class="toolbar">
  <button class="btn" onclick="open_modal('addModal')">➕ Add Lead</button>
  <button class="btn btn-green" onclick="open_modal('importModal')">📥 Import Excel</button>
  <button class="btn btn-purple" onclick="open_modal('offerModal')">🎁 Upload Offer</button>
</div>

<!-- ── LEADS TABLE ── -->
<div class="section">
  <div class="section-card">
    <div class="section-header">📋 All Leads</div>
    <div class="table-filters">
      <span class="filter-label">Filter:</span>
      <input type="text" id="searchInput" placeholder="🔍 Search name or mobile..." oninput="filterTable()">
      <select id="statusFilter" onchange="filterTable()">
        <option value="">All Status</option>
        <option value="new">New</option>
        <option value="active">Active</option>
        <option value="hot">Hot</option>
        <option value="warm">Warm</option>
        <option value="cold">Cold</option>
        <option value="converted">Converted</option>
        <option value="dead">Dead</option>
        <option value="lost_to_codealer">Lost to Co-dealer</option>
        <option value="lost_to_competitor">Lost to Competitor</option>
      </select>
      <select id="spFilter" onchange="filterTable()">
        <option value="">All Salespersons</option>
        <option>Naveen</option>
        <option>Shelindra</option>
      </select>
    </div>
    <table id="leadsTable">
      <thead>
        <tr>
          <th>Customer</th>
          <th>Mobile</th>
          <th>Interested In</th>
          <th>Budget</th>
          <th>Status</th>
          <th>Assigned To</th>
          <th>Last Called</th>
          <th>Next Follow-up</th>
          <th>Calls</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="leadsBody">{rows}</tbody>
    </table>
  </div>
</div>

<!-- ── MODALS ── -->
<div class="modal" id="addModal">
  <div class="mbox">
    <h3>➕ Add New Lead</h3>
    <label>Customer Name</label>
    <input id="f_name" placeholder="Ramesh Kumar">
    <label>Mobile Number *</label>
    <input id="f_mobile" placeholder="9876543210">
    <label>Interested Model</label>
    <select id="f_model">
      <option value="">-- Select Model --</option>
      <option>Splendor Plus</option><option>HF Deluxe</option>
      <option>Passion Pro</option><option>Glamour</option>
      <option>Super Splendor</option><option>Destini 125</option>
      <option>Maestro Edge 125</option><option>Xoom 110</option>
      <option>Xtreme 160R</option><option>Xtreme 125R</option>
      <option>Mavrick 440</option><option>XPulse 200</option>
    </select>
    <label>Budget (₹)</label>
    <input id="f_budget" placeholder="80000">
    <label>Notes</label>
    <textarea id="f_notes" rows="2" placeholder="Any special requirement..."></textarea>
    <div class="row">
      <button class="btn" onclick="addLead()">💾 Save Lead</button>
      <button class="btn" style="background:#999" onclick="close_modal('addModal')">Cancel</button>
    </div>
  </div>
</div>

<div class="modal" id="importModal">
  <div class="mbox">
    <h3>📥 Import Leads from Excel / CSV</h3>
    <p class="hint">Columns needed: name, mobile, interested_model, budget, source</p>
    <input type="file" id="importFile" accept=".xlsx,.xls,.csv">
    <div class="row" style="margin-top:8px">
      <button class="btn btn-green" onclick="importLeads()">Import</button>
      <button class="btn" style="background:#999" onclick="close_modal('importModal')">Cancel</button>
    </div>
    <div id="importResult" style="margin-top:10px;color:#2e7d32;font-size:0.85em"></div>
  </div>
</div>

<div class="modal" id="offerModal">
  <div class="mbox">
    <h3>🎁 Upload Offer / Scheme</h3>
    <label>Offer Title *</label>
    <input id="o_title" placeholder="Diwali Special — ₹5,000 off + Free Accessories">
    <label>Valid Till</label>
    <input id="o_valid" type="date">
    <label>Applicable Models (comma separated)</label>
    <input id="o_models" placeholder="Splendor Plus, HF Deluxe, Glamour">
    <label>Upload File (PDF / Excel / Image)</label>
    <input type="file" id="offerFile" accept=".pdf,.xlsx,.xls,.png,.jpg,.jpeg">
    <div class="row" style="margin-top:8px">
      <button class="btn btn-purple" onclick="uploadOffer()">Upload</button>
      <button class="btn" style="background:#999" onclick="close_modal('offerModal')">Cancel</button>
    </div>
    <div id="offerResult" style="margin-top:10px;color:#2e7d32;font-size:0.85em"></div>
  </div>
</div>

<div id="toastContainer"></div>

<script>
// ── ACTIVE CALLS ──
async function updateActiveCalls() {{
  try {{
    const r = await fetch('/api/active-calls');
    const d = await r.json();
    const el = document.getElementById('activeCalls');
    if (d.active_calls > 0) {{
      el.textContent = `🟢 ${{d.active_calls}} Active Call${{d.active_calls > 1 ? 's' : ''}}`;
      el.style.background = '#e8f5e9';
    }} else {{
      el.textContent = '⚪ No Active Calls';
      el.style.background = '#f5f5f5';
      el.style.color = '#888';
    }}
  }} catch(e) {{}}
}}
updateActiveCalls();
setInterval(updateActiveCalls, 10000);

// ── TABLE FILTER ──
function filterTable() {{
  const search = document.getElementById('searchInput').value.toLowerCase();
  const status = document.getElementById('statusFilter').value.toLowerCase();
  const sp = document.getElementById('spFilter').value.toLowerCase();
  const rows = document.querySelectorAll('#leadsBody tr');
  rows.forEach(row => {{
    const text = row.textContent.toLowerCase();
    const matchSearch = !search || text.includes(search);
    const matchStatus = !status || text.includes(status);
    const matchSp = !sp || text.includes(sp);
    row.style.display = (matchSearch && matchStatus && matchSp) ? '' : 'none';
  }});
}}

// ── MODALS ──
function open_modal(id) {{ document.getElementById(id).classList.add('open'); }}
function close_modal(id) {{ document.getElementById(id).classList.remove('open'); }}
document.querySelectorAll('.modal').forEach(m =>
  m.addEventListener('click', e => {{ if (e.target === m) m.classList.remove('open'); }})
);

function toast(msg, err=false) {{
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toastContainer').appendChild(t);
  setTimeout(() => t.remove(), 4000);
}}

// ── ADD LEAD ──
async function addLead() {{
  const mobile = document.getElementById('f_mobile').value.trim();
  if (!mobile) {{ toast('Mobile number is required!', true); return; }}
  const data = {{
    name: document.getElementById('f_name').value,
    mobile,
    interested_model: document.getElementById('f_model').value,
    budget: document.getElementById('f_budget').value,
    notes: document.getElementById('f_notes').value,
  }};
  const r = await fetch('/api/leads/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(data)
  }});
  const res = await r.json();
  if (res.success) {{
    toast('Lead added! ID: ' + res.lead_id);
    close_modal('addModal');
    setTimeout(() => location.reload(), 1500);
  }} else {{
    toast('Error adding lead', true);
  }}
}}

// ── IMPORT ──
async function importLeads() {{
  const file = document.getElementById('importFile').files[0];
  if (!file) {{ toast('Please select a file', true); return; }}
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/api/leads/import', {{ method: 'POST', body: fd }});
  const res = await r.json();
  document.getElementById('importResult').textContent =
    `Imported: ${{res.imported}} leads | Skipped: ${{res.skipped}} duplicates`;
}}

// ── CALL LEAD ──
async function callLead(leadId, mobile) {{
  if (!confirm(`Call ${{mobile}} now?\\nPriya will call this number immediately.`)) return;
  const r = await fetch('/api/call/make', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ lead_id: leadId, mobile }})
  }});
  const res = await r.json();
  toast(res.message || 'Call initiated!');
}}

// ── UPLOAD OFFER ──
async function uploadOffer() {{
  const title = document.getElementById('o_title').value.trim();
  const file = document.getElementById('offerFile').files[0];
  if (!title) {{ toast('Offer title is required!', true); return; }}
  if (!file) {{ toast('Please select a file', true); return; }}
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', title);
  fd.append('valid_till', document.getElementById('o_valid').value);
  fd.append('models', document.getElementById('o_models').value);
  const r = await fetch('/api/offers/upload', {{ method: 'POST', body: fd }});
  const res = await r.json();
  document.getElementById('offerResult').textContent =
    res.success ? 'Offer uploaded! AI will use this in all calls.' : 'Upload failed';
}}
</script>
</body>
</html>"""


# ── ENTRY POINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
