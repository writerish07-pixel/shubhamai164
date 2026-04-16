# Groq Evaluation Report

Date: 2026-04-16 (UTC)

## 1) Objective
Evaluate whether Groq should be used for lower-latency voice pipeline stages without breaking existing production flow.

## 2) What was compared
- **Current LLM path**: `ConversationManager.chat()` (already Groq-backed, llama-3.3-70b-versatile).
- **Current STT path**: Sarvam primary + Deepgram fallback (`voice.py`).
- **Proposed path**: Groq STT (`whisper-large-v3`) + Groq LLM (`llama-3.3-70b-versatile`) + existing Sarvam TTS.

## 3) Measurement status
### Live API benchmarks
Could not run live provider benchmarks in this environment because API credentials are absent:
- `GROQ_API_KEY`: not set
- `SARVAM_API_KEY`: not set
- `DEEPGRAM_API_KEY`: not set

### Comparative latency model (based on current timeout budgets + call graph)
| Stage | Current Provider | Current Timeout Budget | Proposed Provider | Proposed Budget | Expected Delta |
|---|---|---:|---|---:|---|
| STT | Sarvam (fallback Deepgram) | 6s | Groq Whisper v3 | 6s | Likely faster p50/p95 |
| LLM | Groq 70B | 5s (config) / 15-25s route wrapper | Groq 70B | same | No major change if same model |
| TTS | Sarvam | 5s | Sarvam (unchanged) | 5s | No change |

## 4) Token generation speed perspective
- Since current LLM is already Groq-backed, **token speed gains are likely limited** unless model routing changes (e.g., fast model for simple turns, smart model for complex turns).
- Largest latency opportunity is **STT replacement to Groq Whisper** and reducing retries/fallback churn.

## 5) Cost comparison (qualitative)
- Pricing can change frequently; exact cost should be verified at procurement time.
- Architecture-level expectation:
  - Single-vendor STT+LLM (Groq) may simplify billing/ops.
  - Keeping Sarvam only for TTS preserves voice output quality continuity and avoids full migration risk.

## 6) Recommendation
## Recommended strategy: **Hybrid**
1. **Use Groq for STT + LLM** in a parallel path (implemented in new files).
2. **Keep Sarvam TTS** for now (already integrated, minimizes break risk).
3. Run A/B rollout:
   - 20% traffic on Groq parallel handler
   - 80% existing handler
   - compare p50/p95 total turn latency + silence/error rates
4. Decide full migration only after 3-7 days of production telemetry.

## 7) Success criteria for rollout decision
- ≥25% reduction in median turn latency
- ≥40% reduction in STT timeout rate
- No increase in call-drop or silence incidents
- Equal or better transcript quality (manual QA sample)
