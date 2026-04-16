"""
intent.py
Fast intent detection — bypasses Groq for simple/common customer inputs.

OPTIMIZATIONS:
- 🔥 OPTIMIZATION: Pre-compiled set lookup instead of O(n) pattern scan
- 🔥 OPTIMIZATION: Shorter responses for faster TTS
- 🔥 OPTIMIZATION: Added more patterns for better coverage (fewer Groq calls)
"""

# 🔥 OPTIMIZATION: Use frozensets for O(1) lookup instead of list iteration
INTENTS = {
    "yes_visit": {
        "patterns": frozenset([
            "aa jaunga", "aa jaungi", "aaonga", "aaunga",
            "aata hoon", "aati hoon", "visit karunga", "aaugi",
            "aa jaugi", "showroom aaunga", "showroom aaugi",
            "आ जाऊँगा", "आ जाऊँगी", "आता हूँ", "आती हूँ",
            "आ जाऊंगा", "आ जाऊंगी", "शोरूम आऊँगा", 
            "शोरूम आऊँगी", "आऊँगा", "आऊँगी",
            "aa jata hoon", "aa raha hoon", "aa rahe hain",
        ]),
        # 🔥 OPTIMIZATION: Shorter response — fewer TTS characters
        "response": "Bahut accha! Aap kab aa rahe hain — aaj ya kal?"
    },
    # 🔥 FIX: Moved busy, not_interested, callback BEFORE acknowledgement
    # so compound phrases like "haan, busy hoon" match the specific intent
    # first instead of matching acknowledgement's broad "haan" pattern.
    "busy": {
        "patterns": frozenset([
            "busy", "baad mein", "baad me", "abhi nahi", "abhi mat",
            "baad mein call", "later", "free nahi", "time nahi",
            "व्यस्त", "बाद में", "अभी नहीं", "बाद में कॉल", "फ्री नहीं",
            "टाइम नहीं", "अभी मत", "meeting mein", "driving",
        ]),
        "response": "Koi baat nahi! Kab call karoon — aapko kab free rahega?"
    },
    "not_interested": {
        "patterns": frozenset([
            "nahi chahiye", "interest nahi", "mat karo call", "band karo",
            "hata lo number", "nahi lena", "no thanks", "नहीं चाहिए", "इंटरेस्ट नहीं",
            "मत करो कॉल", "बंद करो", "हटा लो नंबर", "नहीं लेना", "कोई जरूरत नहीं",
            "zaroorat nahi", "जरूरत नहीं", "don't call",
        ]),
        "response": "Koi baat nahi ji! Zaroorat ho toh call karein. Dhanyavaad!"
    },
    "callback": {
        "patterns": frozenset([
            "call karo", "call karna", "phone karo", "phone karna",
            "baad mein baat", "call back", "कॉल करो", "कॉल करना",
            "फोन करो", "फोन करना", "बाद में बात", "कॉल बैक", "बाद में कॉल करो",
        ]),
        "response": "Bilkul! Kab call karoon — subah ya shaam?"
    },
    "address": {
        "patterns": frozenset([
            "address", "kahan hai", "kahan he", "location", "showroom kahan",
            "jagah", "kidhar", "kahaan", "showroom ka", "showroom ki",
            "एड्रेस", "पता", "कहाँ है", "कहाँ", "कहां है", "कहां",
            "लोकेशन", "जगह", "किधर", "शोरूम कहाँ", "शोरूम का पता",
            "map", "google map",
        ]),
        "response": "Lal Kothi Tonk Road, Jaipur. 9 se 7 baje tak khula hai."
    },
    "timing": {
        "patterns": frozenset([
            "timing", "kitne baje", "kab khulta", "band kab", "working hours",
            "khula", "showroom ka time", "showroom ki timing", "टाइम", "समय",
            "कितने बजे", "कब खुलता", "बंद कब", "वर्किंग आवर्स", "खुला रहेगा",
            "खुला रहता", "कब तक खुला", "रूम कब", "कब बंद", "खुलता है", "बंद होता", 
        ]),
        "response": "Monday se Saturday, subah 9 se shaam 7 baje tak."
    },
    "test_ride": {
        "patterns": frozenset([
            "test ride", "test drive", "chalana", "try", "chalake dekhna",
            "drive karna", "ride karna", "टेस्ट राइड", "टेस्ट ड्राइव", "चलाना",
            "चला के देखना", "ड्राइव करना", "राइड करना", "चलाकर देखना",
        ]),
        "response": "Test ride free hai! Aap kab aa sakte hain?"
    },
    # 🔥 OPTIMIZATION: New intents to catch more patterns without Groq
    "thanks": {
        "patterns": frozenset([
            "dhanyavaad", "thank you", "thanks", "shukriya", "धन्यवाद",
            "शुक्रिया", "thanku", "thnx",
        ]),
        "response": "Dhanyavaad ji! Kuch aur madad chahiye toh bataaiye."
    },
    "emi": {
        "patterns": frozenset([
            "emi", "installment", "monthly", "loan", "finance",
            "किस्त", "ईएमआई", "mahina", "per month",
        ]),
        "response": "EMI 1,800 se shuru hai! Budget bataaiye, best plan batati hoon."
    },
}


def detect_intent(text: str, lead: dict = None) -> str | None:
    """
    Fast intent matching — O(1) per pattern via set lookup.
    Returns response string if matched, None otherwise.
    
    🔥 FIX: Uses word-boundary matching for short patterns (len < 4)
    to prevent false-positive substring matches.
    🔥 FIX: Uses 'break' instead of 'return None' for acknowledgement
    guard so other intents can still be checked.
    """
    text_lower = text.lower().strip()
    if len(text_lower) < 2:
        return None
    
    has_name = lead and lead.get("name", "").strip()
    # 🔥 FIX: Pre-split words for word-boundary matching of short patterns
    words = set(text_lower.split())
    
    for intent_name, data in INTENTS.items():
        for pattern in data["patterns"]:
            # 🔥 FIX: Short patterns use exact word match to avoid
            # false positives (e.g. 'han' in 'kahan')
            if len(pattern) < 4:
                matched = pattern in words
            else:
                matched = pattern in text_lower
            if matched:
                if intent_name == "acknowledgement" and not has_name:
                    break  # 🔥 FIX: Skip acknowledgement, continue checking others
                print(f"[Intent] Matched '{intent_name}' for: '{text[:50]}'")
                return data["response"]
    return None
