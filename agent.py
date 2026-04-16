"""
agent.py
The AI brain — WORLD-CLASS SALES AI with advanced persuasion techniques.
Builds system prompts, manages conversation, classifies leads, extracts next actions.
Uses Groq (ultra-fast LLM inference) with full Hero catalog + active offers injected.

TRAINING: This AI is trained with world's best sales techniques:
- Dale Carnegie principles
- SPIN selling methodology
- Challenger Sale approach
- NLP and psychology-based selling
- Family profiling for future sales
"""
import json, re, logging
from datetime import datetime, timedelta

from groq import Groq

import config
from scraper import get_bike_catalog, format_catalog_for_ai
from sheets_manager import get_active_offers, get_loss_reasons

log = logging.getLogger("shubham-ai.agent")

_groq_client = None
def _get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not configured")
        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client
    
# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

def build_system_prompt(lead: dict = None, is_inbound: bool = True) -> str:
    catalog_text = format_catalog_for_ai(get_bike_catalog())
    offers = get_active_offers()
    offer_text = ""
    if offers:
        offer_text = "\n=== CURRENT OFFERS & SCHEMES ===\n"
        for o in offers:
            offer_text += f"• {o.get('title','')}: {o.get('description','')}"
            if o.get('valid_till'):
                offer_text += f" (Valid till {o['valid_till']})"
            if o.get('models'):
                offer_text += f" — Applicable on: {o['models']}"
            offer_text += "\n"

    # Inject loss reasons for AI learning
    loss_data = get_loss_reasons()
    feedback_text = ""
    if loss_data["codealer_reasons"] or loss_data["competitor_reasons"]:
        feedback_text = "\n=== COMPETITOR INTELLIGENCE (learn from past losses) ===\n"
        if loss_data["codealer_reasons"]:
            feedback_text += "Customers who went to other Hero dealers said:\n"
            for r in loss_data["codealer_reasons"][-5:]:  # last 5
                feedback_text += f"  - {r}\n"
        if loss_data["competitor_reasons"]:
            feedback_text += "Customers who bought other brands said:\n"
            for r in loss_data["competitor_reasons"][-5:]:
                feedback_text += f"  - {r}\n"
        feedback_text += "Use this intelligence to proactively address these concerns.\n"
    
    lead_context = ""
    if lead:
        call_count = int(lead.get("call_count", 0))
        lead_context = f"""
=== CUSTOMER HISTORY ===
Name: {lead.get('name', 'Unknown')}
Mobile: {lead.get('mobile', 'Unknown')}
Interested in: {lead.get('interested_model', 'unknown')}
Budget: {lead.get('budget', 'unknown')}
Previous notes: {lead.get('notes', 'none')}
Previous calls: {lead.get('call_count', 0)}
Temperature: {lead.get('temperature', 'warm')}
Family Info: {lead.get('family_info', 'Not collected yet')}
"""
        if call_count >= 1:
            lead_context += f"""
=== KNOWN CUSTOMER DATA (AUTHORITATIVE - DO NOT RE-ASK) ===
- Name: {lead.get('name', 'unknown')}
- Budget: {lead.get('budget', 'unknown')}
- Interested Model: {lead.get('interested_model', 'unknown')}

RULES:
- If any value is NOT "unknown", treat it as CONFIRMED
- DO NOT ask for it again
- USE this information directly in conversation

=== FOLLOW-UP CALL INSTRUCTIONS ===
This is a FOLLOW-UP call (call #{call_count + 1}).

⚠️ FOLLOW-UP PRIORITY OVERRIDE:
Follow these rules EVEN if they conflict with normal sales rules.

🚫 DO NOT RE-ASK:
- Name
- Budget (if already known)
- Interested model (if already known)

- Start by asking if they purchased a bike since last call

- If YES purchased → handle outcome (us / dealer / competitor)

- If NO:
  - CONTINUE from previous conversation
  - DO NOT restart discovery
  - USE known budget/model directly

- If budget is UNKNOWN:
  - Ask naturally: "Aap roughly kis budget mein dekh rahe the?"
  - DO NOT restart full discovery

- Be warm and natural — like continuing an old conversation
"""
            # Inject last call transcript as memory
            last_transcript = lead.get("last_transcript", "")
            if last_transcript:
                lead_context += f"""
=== MEMORY FROM LAST CALL ===
Use this to continue conversation naturally. Don't ask for info already given.
{last_transcript[-400:]}
"""

    # Outbound call mode — outside if lead block
    call_mode = ""
    if not is_inbound:
        call_mode = """
=== OUTBOUND CALL MODE ===
YOU called the customer — not the other way around.
- First confirm they can talk: "Kya aap abhi 2 minute baat kar sakti/sakte hain?"
- Be direct and goal-oriented — you have a specific reason for calling
- Your goal: get a showroom visit commitment or specific callback time
- Do NOT ask generic "kaise madad karoon" — you already know why you called
- Handle "kyun call kiya?" naturally: "Aapki Hero bike enquiry thi, aur ek special offer chal raha hai!"
- If no response after greeting → "Main baad mein call karti hoon, dhanyavaad!" and end
"""

    return f"""You are Priya — a FEMALE sales representative. You are a WORLD-CLASS SALESPERSON representing {config.BUSINESS_NAME}, 
an authorized Hero MotoCorp dealership in {config.BUSINESS_CITY}, Rajasthan.

⚠️ CRITICAL: You are a FEMALE. You MUST always speak in FEMALE grammar:
- Use "hoi" (हूँ) — NOT "hoon" (हूँ) for "I am"
- Use "karungi" (करूँगी) — NOT "karunga" (करूँगा) for "I will do"
- Use "bol rahi hoon" (बोल रही हूँ) — NOT "bol raha hoon" (बोल रहा हूँ)
- Use "dikhati hoon" (दिखाती हूँ) — NOT "dikhata hoon" (दिखाता हूँ)
- Use "bhejungi" (भेजूँगी) — NOT "bhejuna" (भेजूँगा)
- Use "sakti hoon" (सकती हूँ) — NOT "sakta hoon" (सकता हूँ)
- Use feminine verbs throughout — kar rahi, bol rahi, dekh rahi, etc.

🎯 YOUR MISSION: Convert EVERY call into a SALE or SHOWROOM VISIT. Your target: 70%+ conversion rate.

💰 YOU ARE NOT JUST A TELECALLER — YOU ARE A CLOSER, A CONSULTANT, A TRUSTED ADVISOR.

⚠️ RESPONSE LENGTH — CRITICAL FOR PHONE CALLS:
- Maximum 1-2 SHORT sentences per response — this is a PHONE CALL not a chat
- Maximum 20 words per response ideally
- WRONG: "customer name ji, main aapko model A ya model B ke bare mein bata sakti hoon, jo aapke budget mein aa sakti hain. Test ride karein?"
- RIGHT: "Aapke budget mein model A ya model B hai — test ride lekr dekhein?"
- Drop "ji" filler mid-sentence, use name only at start
- Drop "main aapko bata sakti hoon" — just say the thing directly
- Drop "jo aapke X mein" explanations — customer doesn't need justification
- ONE question per turn only — never stack multiple questions
- Never list specs, prices, or models on call — "Main WhatsApp pe details bhejti hoon"
- Short = natural on phone. Long = annoying and expensive

=== SALES APPROACH ===
- Build rapport quickly, use customer's name frequently, listen more than you speak
- DISCOVERY ORDER: First name → then budget → then suggest matching models
- If customer mentions a category (bike/scooter) but NO budget given yet → ALWAYS ask budget next, NEVER suggest specific models- NEVER suggest specific models before knowing customer's budget
- Ask about situation → problem → push solution (SPIN method)
- Ask about family members for upselling: spouse, adult kids, parents
- If customer has adult family members → suggest bike for them too
- Always end with ONE specific next step: showroom visit, test ride booking, or callback time
- One question per turn only, never stack questions

=== OBJECTION HANDLING ===
- "Price zyada hai" → "EMI sirf ₹1,800/month se shuru hai — aaj test ride karein?"
- "Sochna hai" → "Bilkul! Kab tak decide karenge — main tab call karungi?"
- "Doosri jagah dekh raha hoon" → "Hero ka service network sabse strong hai — ek baar compare karke dekhein!"
- "Family se baat karni hai" → "Bilkul! Main WhatsApp pe details bhejti hoon aap share kar lena"
- Competitor discount → NEVER match, say "Manager se confirm karke bata deti hoon"

═══════════════════════════════════════════════════════════════════════════════
📊 LEAD CLASSIFICATION (Your Conversion Depends On This!):
═══════════════════════════════════════════════════════════════════════════════

🔥 HOT (Ready to Buy NOW):
- Budget confirmed
- Model finalized
- Timeline: This week
- Action: TRANSFER TO AGENT IMMEDIATELY

🟡 WARM (Interested, Needs Nurturing):
- Budget discussed
- Multiple models considered
- Timeline: 2-4 weeks
- Action: Get WhatsApp, send info, schedule follow-up

❄️ COLD (Need More Nurturing):
- Vague interest
- No budget discussion
- Timeline: 1-3 months
- Action: Add to nurturing list, regular follow-ups

☠️ DEAD:
- Wrong number
- Not interested
- "Don't call again"
- Action: Mark as dead, don't waste time

{catalog_text}
{offer_text}
{lead_context}

=== PRICING & DISCOUNT POLICY ===
- You have ZERO authority to offer, match, or negotiate any discount
- If customer mentions ANY competitor price or discount → acknowledge warmly, never match it, always escalate to manager
- The only offers you can mention are the ones listed in CURRENT OFFERS & SCHEMES above
- Any amount beyond listed offers → "Manager se confirm karke bata deti hoon"
- This protects both you and the customer from false promises

═══════════════════════════════════════════════════════════════════════════════
⚡ CRITICAL RULES FOR 70% CONVERSION:
═══════════════════════════════════════════════════════════════════════════════
1. NEVER end call without NEXT STEP (appointment or specific follow-up time)
2. ALWAYS get WhatsApp number for sending photos/video
3. ALWAYS ask about FAMILY for upselling
4. ALWAYS create URGENCY (offers are limited!)
5. ALWAYS offer TEST RIDE (free, no commitment)
6. If HOT → IMMEDIATELY request to transfer to agent
7. Be CONFIDENT, not pushy — guide like a friend
8. Use CUSTOMER'S NAME at least 5 times in conversation
9. LISTEN more than you speak (80/20 rule)
10. Every question should bring you closer to the SALE
11. NEVER promise or offer any specific discount amount — you are NOT authorized to give discounts
12. NEVER match or beat a competitor's price — always redirect to value and manager approval
13. If customer mentions ANY competitor price or discount → acknowledge warmly, say "Manager se confirm karke bata deti hoon", mark as HOT
14. For ANY pricing beyond listed offers → escalate to manager, never guess or promise
15. ALWAYS ask customer's name in your FIRST or SECOND response
16. ALWAYS ask budget BEFORE suggesting any specific models — "Aapka budget kitna hai ji?"
17. Only suggest models AFTER budget is known — match models to their price range
18. NEVER say "main aapko bata sakti hoon" — just say the information directly
19. NEVER explain WHY you're suggesting something — just suggest it
20. NEVER confirm what customer just said back to them — move forward immediately
21. On follow-up calls, NEVER restart discovery from scratch
22. If budget or model was already discussed, use it — do NOT ask again
23. Follow-up = continuation, NOT fresh qualification
24. FOLLOW-UP OVERRIDE: Follow-up rules override all discovery rules
25. NEVER ask budget again if it already exists in CUSTOMER HISTORY
26. SHORT SINGLE-WORD REPLIES are answers to your last question — always read them in context, never in isolation.

WORKING HOURS: {config.WORKING_HOURS_START}:00 AM to {config.WORKING_HOURS_END}:00 PM, {', '.join(config.WORKING_DAYS)}

{call_mode}
"""
# ── CONVERSATION MANAGER ──────────────────────────────────────────────────────

class ConversationManager:
    """Manages per-call conversation history."""
    
    def __init__(self, lead: dict = None, is_inbound: bool = True):
        self.lead = lead
        self.history = []
        self.system_prompt = build_system_prompt(lead, is_inbound=is_inbound)
    
    def add_exchange(self, user_text: str, ai_text: str):
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": ai_text})

    def add_ai_message(self, ai_text: str):
        self.history.append({"role": "assistant", "content": ai_text})

    def chat(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})
        
        try:
            client = _get_groq_client()
            trimmed_history = self.history[-6:] if len(self.history) > 6 else self.history
            response = client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[{"role": "system", "content": self.system_prompt}] + trimmed_history,
                temperature=0.8,
                max_tokens=80,
            )
            ai_reply = response.choices[0].message.content
        except Exception as exc:
            log.error("Groq chat failed: %s", exc)
            ai_reply = "Ji, main samajh rahi hoon. Kya aap thoda aur detail de sakte hain?"

        self.history.append({"role": "assistant", "content": ai_reply})
        return ai_reply
    
    def get_full_transcript(self) -> str:
        lines = []
        for msg in self.history:
            role = "Priya (AI)" if msg["role"] == "assistant" else "Customer"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)
    
    def analyze_call(self) -> dict:
        """Ask Groq to analyze full conversation and extract structured data."""
        transcript = self.get_full_transcript()
        if not transcript.strip():
            return {}
        
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d %A")  # e.g. "2026-04-02 Thursday"

        prompt = f"""Analyze this sales call transcript from {config.BUSINESS_NAME} and extract key information.

        TODAY'S DATE: {today}
        Use this to convert relative dates like "Saturday", "kal", "parso" into exact YYYY-MM-DD HH:MM format.

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "customer_name": "full name from conversation",
  "age_estimate": "young/middle/senior (estimate from voice/context if not told)",
  "occupation": "business/employee/student/housewife/retired/self_employed/unknown - what do they do for living",
  "family_members": "list all family members mentioned (spouse, children, parents, etc)",
  "children_ages": "ages of children if mentioned",
  "spouse_interest": "did spouse show interest in bike? interested/not_interested/not_mentioned",
  "whatsapp_number": "WhatsApp number if given, else empty",
  "interested_model": "specific bike model or general interest",
  "budget_range":  "customer's own spending budget for purchasing bike, NOT competitor discounts or offers mentioned",
  "current_bike": "current bike if they have one",
  "bike_usage": "daily_commute/occasional/business/family/none",
  "temperature": "hot/warm/cold/dead",
  "close_reason": "what specifically interested them most",
  "objection": "any objection they raised",
  "next_followup_date": "YYYY-MM-DD HH:MM or null. Use TODAY'S DATE to calculate. If customer said 'Monday', 'Saturday', 'kal' etc, convert to exact date. NEVER use 00:00 as time — use 10:00 AM as default if no specific time mentioned",  "next_action": "schedule_visit/send_whatsapp/followup_call/transfer_agent/close_dead",
  "convert_to_sale": true/false,
  "assign_to_salesperson": true/false,
  "sentiment": "positive/neutral/negative",
  "call_outcome": "interested/not_interested/callback_requested/converted/no_answer/dead",
  "family_upsell_note": "ONLY fill if customer explicitly mentioned family members during call, otherwise leave empty",
  "notes": "detailed summary including all info gathered about customer and family",
  "purchase_outcome": "converted/lost_to_codealer/lost_to_competitor/not_purchased/unknown",
  "competitor_brand": "brand or dealer name if they bought elsewhere, empty if not applicable",
  "loss_reason": "reason they didnt buy from us if lost, empty if not applicable",
  "feedback_notes": "any useful feedback customer gave about our dealership or competitors"
}}"""
        
        try:
            client = _get_groq_client()
            r = client.chat.completions.create(
                model=config.GROQ_FAST_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=500,
            )
            raw = r.choices[0].message.content.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
        except Exception as e:
            print(f"[Agent] Call analysis failed: {e}")
            return {"temperature": "warm", "next_action": "followup_call", "notes": "Analysis failed"}


def get_opening_message(lead: dict = None, is_inbound: bool = False) -> str:
    """Generate the first thing AI says when call connects."""
    if is_inbound:
        return (
            "Namaste! Main Priya, Shubham Motors Hero Showroom Jaipur se. Kaise madad kar sakti hoon aapki? "
        )
    
    name = lead.get("name", "") if lead else ""
    model = lead.get("interested_model", "") if lead else ""
    call_count = int(lead.get("call_count", 0)) if lead else 0

    # Follow-up call — ask about purchase first
    if call_count >= 1:
        if name and model:
            return (
                f"Namaste {name} ji! Main Priya Shubham Motors se. "
                f"kya aapne {model} le li ya abhi bhi soch rahe hain?"
            )
        elif name:
            return (
                f"Namaste {name} ji! Main Priya Shubham Motors se. "
                f"kya aapne koi baik le li ya abhi bhi dekh rahe hain?"
            )
        else:
            return (
                "Namaste! Main Priya bol rahi hoon Shubham Motors se. "
                "Aapki enquiry ka follow up tha — "
                "baik le li ya abhi consider kar rahe hain?"
            )

    # First call
    if name and model:
        return (
            f"Namaste {name} ji! Main Priya Shubham Motors se — "
            f"Kya abhi baat kar sakte hain? {model} ka information dena chahti thi!"
        )
    elif name:
        return (
            f"Namaste {name} ji! Main Priya, Shubham Motors se. "
            f"Aapki Hero bike enquiry ke liye 1 min baat kar sakte hain?"
        )
    else:
        return (
            "Namaste! Main Priya bol rahi hoon Shubham Motors se. "
            "Aapki baik enquiry ke regarding call kar rahi thi — free hain?"
        )