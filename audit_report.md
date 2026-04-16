# AI Voice System Audit Report

Date: 2026-04-16 (UTC)
Scope: FastAPI + Exotel webhook flow + STT/LLM/TTS + URL generation

## 1) Executive Summary

### Primary root causes for call silence
1. **Static `PUBLIC_URL` is hardcoded into webhook XML/action URLs**. If this value is stale, private, localhost, or mismatched with runtime ingress domain, Exotel cannot fetch the next audio/record endpoint and the caller hears silence.  
2. **Blocking sync STT/TTS operations wrapped in executor with aggressive timeout windows** can return `None`, creating fallback paths that may still fail when URLs/audio are unavailable.  
3. **Audio-file lifecycle race risks**: response audio is persisted and then served, but if URL is inaccessible or file not present at serve time, playback fails.

## 2) Micro Audit (Code-Level)

### A. API calls: timeout & retry posture
- **Exotel REST calls** have retry logic (`_request_with_retry`) for transient network errors with backoff. Good resilience baseline.  
- **Recording download** in `main.py` uses `requests.get(..., timeout=15)` without retry; transient failures can drop user turn.  
- **STT/TTS timeouts** are set low in config (`STT_TIMEOUT_SEC=6`, `TTS_TIMEOUT_SEC=5` default), good for responsiveness but may cause false timeouts under load.

### B. STT → LLM → TTS flow correctness
- `/call/gather/{call_sid}` correctly tries `RecordingUrl` first, runs STT, then intent/LLM, then TTS.  
- On empty STT result, silence counter increments; after 3 turns call hangs up.  
- Fallback `<Say>` exists when TTS audio is unavailable.

### C. Audio creation & playback path
- Opening audio and response audio are written to `uploads/` and served through `/call/audio/...` endpoints.
- Risk: Exotel playback depends on externally reachable absolute URL; any mismatch in base URL leads to silent playback.

### D. Threading / async correctness
- `main.py` is async; heavy sync calls are offloaded with `_run(...run_in_executor...)`.
- Potential contention: thread pool fixed at 12; concurrent calls may queue STT/TTS/LLM work, increasing latency and timeout probability.

### E. Error handling
- Main gather block has broad `try/except` and safe fallback XML.
- Some helper functions return empty bytes on failure (non-exception path), which can hide root causes unless logs are monitored.

### F. Blocking calls inside async
- Design intentionally wraps sync functions with executor, reducing direct event loop blocking.
- However, some paths still use sync operations with larger I/O durations; throughput scales with thread pool, not event loop.

## 3) Macro Audit (Architecture-Level)

## Call flow checked
`Exotel -> /call/handler or /call/incoming -> <Record action=/call/gather/{sid}> -> STT -> LLM -> TTS -> <Play> -> repeat`

### Latency bottlenecks
1. Recording fetch from Exotel (`_download_recording`)  
2. STT provider round-trip  
3. LLM generation  
4. TTS synthesis  
5. Exotel fetching generated audio URL

### High-probability failure points causing silence
- Invalid/stale `PUBLIC_URL`
- Exotel cannot fetch `/call/audio/response/{sid}` due to domain/TLS/routing mismatch
- TTS timeout/empty payload followed by weak fallback path (or fallback not audible due downstream URL problem)
- Recording download failure causing repeated silence retries

### URL generation consistency
- Multiple endpoints construct URLs from `config.PUBLIC_URL`; this is consistent internally but fragile operationally if deployment URL changes.

### External dependency risks
- Exotel availability
- Sarvam availability (current STT+TTS primary)
- Groq availability (LLM already used in `agent.py`)
- Network-level variability directly impacts call UX

## 4) Ngrok Dependency Findings

### Found
- Explicit config symbol remains: `NGROK_AUTH_TOKEN` in `config.py`.
- Startup warning text still says `use ngrok` when `PUBLIC_URL` is localhost.

### Impact
- Not a runtime hard dependency, but operational messaging/config semantics still assume ngrok in places.

### Recommended fix strategy (no breaking change)
1. Keep `PUBLIC_URL` support for static deployments.
2. Add runtime URL resolver in webhook handlers (`request.base_url` / trusted forwarded headers).
3. Use resolved runtime URL for Exotel XML action/playback links.
4. Update warning strings to generic “public HTTPS URL required” (remove ngrok wording).

## 5) Risk Register

| Risk | Severity | Likelihood | Notes |
|---|---|---:|---|
| Stale/invalid `PUBLIC_URL` | Critical | High | Direct silence trigger |
| Thread-pool saturation under concurrent calls | High | Medium | Raises end-to-end latency |
| Provider timeouts (STT/TTS) | High | Medium | Causes fallback / retries |
| Exotel recording download transient failure | Medium | Medium | Creates empty-input loops |
| Weak observability/trace IDs across stages | Medium | Medium | Slower incident diagnosis |

## 6) Recommended Immediate Actions
1. Deploy runtime URL resolution for webhook XML generation (parallel handler added).
2. Add per-turn structured telemetry (`download_ms`, `stt_ms`, `llm_ms`, `tts_ms`, `total_ms`).
3. Increase resiliency: retry recording download once with short backoff.
4. Preserve `<Say>` textual fallback for all TTS failures.
5. Keep existing system intact; rollout Groq path behind route/flag for A/B testing.
