"""
sheets_manager.py
Google Sheets primary storage with local JSON fallback.
Tabs: Leads, Calls, Offers, Settings, Catalog, FAQ
"""
import json
import time
from datetime import datetime
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
import config

# ── LOCAL FALLBACK SETUP ──────────────────────────────────────────────────────
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
LEADS_FILE   = DATA_DIR / "leads.json"
CALLS_FILE   = DATA_DIR / "calls.json"
OFFERS_FILE  = DATA_DIR / "offers.json"

# ── GOOGLE SHEETS SETUP ───────────────────────────────────────────────────────
_gc = None
_sheet = None

def _get_sheet():
    global _gc, _sheet
    if _sheet:
        return _sheet
    try:
        creds_dict = config.GOOGLE_CREDENTIALS
        if not creds_dict:
            print("[Sheets] No credentials found, using local JSON")
            return None
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _gc = gspread.authorize(creds)
        _sheet = _gc.open_by_key(config.GOOGLE_SHEET_ID)
        print("[Sheets] ✅ Connected to Google Sheets")
        return _sheet
    except Exception as e:
        print(f"[Sheets] ❌ Connection failed: {e}, using local JSON fallback")
        return None

def _get_tab(tab_name: str):
    sheet = _get_sheet()
    if not sheet:
        return None
    try:
        return sheet.worksheet(tab_name)
    except Exception as e:
        print(f"[Sheets] Tab '{tab_name}' not found: {e}")
        return None

# ── LOCAL HELPERS ─────────────────────────────────────────────────────────────
def _load(filepath: Path) -> list:
    if not filepath.exists():
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save(filepath: Path, data: list):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _rows_to_dicts(tab) -> list:
    try:
        records = tab.get_all_records()
        return [{k.lower(): v for k, v in r.items()} for r in records]
    except Exception as e:
        print(f"[Sheets] Read failed: {e}")
        return []

def _find_row(tab, key_col: str, key_val: str) -> tuple:
    try:
        records = tab.get_all_records()
        headers = tab.row_values(1)
        headers_lower = [h.lower() for h in headers]
        if key_col.lower() not in headers_lower:
            print(f"[Sheets] Column '{key_col}' not found in headers: {headers}")
            return None, None
        col_idx = headers_lower.index(key_col.lower()) + 1
        for i, record in enumerate(records):
            record_lower = {k.lower(): v for k, v in record.items()}
            if str(record_lower.get(key_col.lower(), "")) == str(key_val):
                return i + 2, record
        return None, None
    except Exception as e:
        print(f"[Sheets] Find row failed: {e}")
        return None, None

# ── LEADS ─────────────────────────────────────────────────────────────────────

def add_lead(lead: dict) -> str:
    lead_id = f"L{int(datetime.now().timestamp())}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_lead = {
        "lead_id":          lead_id,
        "name":             lead.get("name", ""),
        "mobile":           lead.get("mobile", ""),
        "interested_model": lead.get("interested_model", ""),
        "budget":           lead.get("budget", ""),
        "source":           lead.get("source", "manual"),
        "status":           "new",
        "temperature":      "warm",
        "assigned_to":      "",
        "assigned_mobile":  "",
        "call_count":       0,
        "last_called":      "",
        "next_followup":    "",
        "notes":            lead.get("notes", ""),
        "created_at":       now,
        "converted_at":     "",
        "tags":             lead.get("tags", ""),
        "purchase_outcome":   "",
        "competitor_brand":   "",
        "loss_reason":        "",
        "feedback_notes":     "",
    }

    # Try Google Sheets first
    tab = _get_tab("Leads")
    if tab:
        try:
            headers = tab.row_values(1)
            row = [str(new_lead.get(h.lower(), "")) for h in headers]
            tab.append_row(row)
            print(f"[Sheets] Lead {lead_id} added to Google Sheets")
        except Exception as e:
            print(f"[Sheets] Lead append failed: {e}, saving locally")
            _save_local_lead(new_lead)
    else:
        _save_local_lead(new_lead)

    return lead_id

def _save_local_lead(lead: dict):
    leads = _load(LEADS_FILE)
    leads.append(lead)
    _save(LEADS_FILE, leads)

def get_all_leads() -> list:
    tab = _get_tab("Leads")
    if tab:
        try:
            return _rows_to_dicts(tab)
        except Exception as e:
            print(f"[Sheets] get_all_leads failed: {e}, using local")
    return _load(LEADS_FILE)

def get_lead_by_mobile(mobile: str) -> dict | None:
    def normalize(num):
        n = str(num).replace("+91", "").replace(" ", "").strip()
        if n.startswith("0"):
            n = n[1:]
        return n

    clean = normalize(mobile)
    for r in get_all_leads():
        if normalize(r.get("mobile", "")) == clean:
            return r
    return None

def get_lead_by_id(lead_id: str) -> dict | None:
    for r in get_all_leads():
        if str(r.get("lead_id", "")) == lead_id:
            return r
    return None

def update_lead(lead_id: str, updates: dict) -> bool:
    tab = _get_tab("Leads")
    if tab:
        try:
            row_idx, existing = _find_row(tab, "lead_id", lead_id)
            if row_idx:
                headers = tab.row_values(1)  
                headers_lower = [h.lower() for h in headers]
                for key, val in updates.items():
                    if key.lower() in headers_lower:
                        col_idx = headers_lower.index(key.lower()) + 1
                        tab.update_cell(row_idx, col_idx, str(val))
                print(f"[Sheets] Lead {lead_id} updated")
                return True
        except Exception as e:
            print(f"[Sheets] update_lead failed: {e}, updating locally")

    # Local fallback
    leads = _load(LEADS_FILE)
    for lead in leads:
        if str(lead.get("lead_id", "")) == lead_id:
            lead.update(updates)
            _save(LEADS_FILE, leads)
            return True
    return False

def get_leads_due_for_followup() -> list:
    now = datetime.now()
    due = []
    for r in get_all_leads():
        if r.get("status") in ("dead", "converted", "lost_to_codealer", "lost_to_competitor"):
            continue
        nf = r.get("next_followup", "")
        if not nf:
            continue
        try:
            nf_dt = datetime.strptime(str(nf), "%Y-%m-%d %H:%M")
            if nf_dt <= now:
                due.append(r)
        except Exception:
            pass
    return due

def get_new_uncontacted_leads() -> list:
    return [r for r in get_all_leads() if r.get("status") == "new" and not r.get("last_called")]

# ── CALL LOG ──────────────────────────────────────────────────────────────────

def log_call(data: dict):
    log_id = f"C{int(datetime.now().timestamp())}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    record = {
        "log_id":       log_id,
        "lead_id":      data.get("lead_id", ""),
        "mobile":       data.get("mobile", ""),
        "direction":    data.get("direction", "outbound"),
        "duration_sec": data.get("duration_sec", 0),
        "status":       data.get("status", ""),
        "transcript":   data.get("transcript", "")[:1000],  # cap at 1000 chars
        "sentiment":    data.get("sentiment", "neutral"),
        "ai_summary":   data.get("ai_summary", ""),
        "next_action":  data.get("next_action", ""),
        "called_at":    now,
    }

    tab = _get_tab("Calls")
    if tab:
        try:
            headers = tab.row_values(1)
            row = [str(record.get(h.lower(), "")) for h in headers]
            tab.append_row(row)
            print(f"[Sheets] Call {log_id} logged to Google Sheets")
            return log_id
        except Exception as e:
            print(f"[Sheets] log_call failed: {e}, saving locally")

    calls = _load(CALLS_FILE)
    calls.append(record)
    _save(CALLS_FILE, calls)
    return log_id

# ── OFFERS ────────────────────────────────────────────────────────────────────

def get_active_offers() -> list:
    tab = _get_tab("Offers")
    if tab:
        try:
            offers = _rows_to_dicts(tab)
        except Exception:
            offers = _load(OFFERS_FILE)
    else:
        offers = _load(OFFERS_FILE)

    today = datetime.now().date()
    active = []
    for r in offers:
        vt = r.get("valid_till", "")
        try:
            if datetime.strptime(str(vt), "%Y-%m-%d").date() >= today:
                active.append(r)
        except Exception:
            active.append(r)
    return active

def add_offer(offer: dict) -> str:
    oid = f"O{int(datetime.now().timestamp())}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    record = {
        "offer_id":    oid,
        "title":       offer.get("title", ""),
        "description": offer.get("description", ""),
        "valid_till":  offer.get("valid_till", ""),
        "models":      offer.get("models", ""),
        "uploaded_at": now,
    }

    tab = _get_tab("Offers")
    if tab:
        try:
            headers = tab.row_values(1)
            row = [str(record.get(h.lower(), "")) for h in headers]
            tab.append_row(row)
            return oid
        except Exception as e:
            print(f"[Sheets] add_offer failed: {e}, saving locally")

    offers = _load(OFFERS_FILE)
    offers.append(record)
    _save(OFFERS_FILE, offers)
    return oid

# ── CATALOG ───────────────────────────────────────────────────────────────────

def get_catalog() -> list:
    """Fetch bike catalog from Google Sheets Catalog tab."""
    tab = _get_tab("Catalog")
    if tab:
        try:
            return _rows_to_dicts(tab)
        except Exception as e:
            print(f"[Sheets] get_catalog failed: {e}")
    return []

# ── FAQ ───────────────────────────────────────────────────────────────────────

def get_faq() -> list:
    """Fetch FAQ entries from Google Sheets FAQ tab."""
    tab = _get_tab("FAQ")
    if tab:
        try:
            return _rows_to_dicts(tab)
        except Exception as e:
            print(f"[Sheets] get_faq failed: {e}")
    return []

# ── FEEDBACK ─────────────────────────────────────────────────────────────────

def get_loss_reasons() -> dict:
    """Aggregate loss reasons from all lost leads for AI learning."""
    all_leads = get_all_leads()
    codealer_reasons = []
    competitor_reasons = []
    
    for lead in all_leads:
        outcome = lead.get("purchase_outcome", "")
        reason = lead.get("loss_reason", "")
        brand = lead.get("competitor_brand", "")
        
        if not reason:
            continue
            
        if outcome == "lost_to_codealer":
            codealer_reasons.append(reason)
        elif outcome == "lost_to_competitor":
            entry = f"{brand}: {reason}" if brand else reason
            competitor_reasons.append(entry)
    
    return {
        "codealer_reasons": codealer_reasons,
        "competitor_reasons": competitor_reasons
    }

# ── SETTINGS ──────────────────────────────────────────────────────────────────

SETTINGS_FILE = DATA_DIR / "settings.json"

def get_setting(key: str, default="") -> str:
    tab = _get_tab("Settings")
    if tab:
        try:
            records = _rows_to_dicts(tab)
            for r in records:
                if r.get("key") == key:
                    return str(r.get("value", ""))
        except Exception as e:
            print(f"[Sheets] get_setting failed: {e}")

    # Local fallback
    settings = _load(SETTINGS_FILE) if SETTINGS_FILE.exists() else []
    for r in settings:
        if r.get("key") == key:
            return str(r.get("value", ""))
    return default

def set_setting(key: str, value: str):
    tab = _get_tab("Settings")
    if tab:
        try:
            row_idx, existing = _find_row(tab, "key", key)
            if row_idx:
                tab.update_cell(row_idx, 2, str(value))
            else:
                tab.append_row([key, str(value)])
            return
        except Exception as e:
            print(f"[Sheets] set_setting failed: {e}")

    # Local fallback
    settings = _load(SETTINGS_FILE) if SETTINGS_FILE.exists() else []
    for r in settings:
        if r.get("key") == key:
            r["value"] = value
            _save(SETTINGS_FILE, settings)
            return
    settings.append({"key": key, "value": value})
    _save(SETTINGS_FILE, settings)

# ── GET STATS FOR DASHBOARD ──────────────────────────────────────────────────────

def get_call_stats() -> dict:
    """Aggregate call statistics from Calls tab for dashboard charts."""
    calls = []
    tab = _get_tab("Calls")
    if tab:
        try:
            calls = _rows_to_dicts(tab)
        except Exception as e:
            print(f"[Sheets] get_call_stats failed: {e}")
            calls = _load(CALLS_FILE)
    else:
        calls = _load(CALLS_FILE)

    today = datetime.now().strftime("%Y-%m-%d")

    # Initialise accumulators
    total_calls = len(calls)
    calls_today = 0
    total_duration = 0
    sentiment = {"positive": 0, "neutral": 0, "negative": 0}
    hourly = {str(h): 0 for h in range(24)}

    for c in calls:
        # Calls today
        called_at = str(c.get("called_at", ""))
        if called_at.startswith(today):
            calls_today += 1

        # Duration
        try:
            total_duration += int(c.get("duration_sec", 0))
        except Exception:
            pass

        # Sentiment
        s = str(c.get("sentiment", "neutral")).lower()
        if s in sentiment:
            sentiment[s] += 1
        else:
            sentiment["neutral"] += 1

        # Hourly activity
        try:
            hour = called_at[11:13]  # extract HH from "YYYY-MM-DD HH:MM"
            if hour in hourly:
                hourly[hour] += 1
        except Exception:
            pass

    avg_duration = round(total_duration / total_calls, 0) if total_calls > 0 else 0

    # Per-salesperson summary
    all_leads = get_all_leads()
    salesperson_stats = {}
    for lead in all_leads:
        sp = lead.get("assigned_to", "")
        if not sp:
            continue
        if sp not in salesperson_stats:
            salesperson_stats[sp] = {
                "name": sp,
                "leads": 0,
                "hot": 0,
                "converted": 0,
                "followups_due": 0,
            }
        salesperson_stats[sp]["leads"] += 1
        if lead.get("temperature") == "hot":
            salesperson_stats[sp]["hot"] += 1
        if lead.get("status") == "converted":
            salesperson_stats[sp]["converted"] += 1

        # Check followup due
        nf = lead.get("next_followup", "")
        if nf:
            try:
                nf_dt = datetime.strptime(str(nf), "%Y-%m-%d %H:%M")
                if nf_dt <= datetime.now():
                    salesperson_stats[sp]["followups_due"] += 1
            except Exception:
                pass

    # Model interest
    model_interest = {}
    for lead in all_leads:
        model = lead.get("interested_model", "").strip()
        if not model:
            continue
        model_interest[model] = model_interest.get(model, 0) + 1

    # Loss reasons
    loss_summary = {"lost_to_codealer": 0, "lost_to_competitor": 0}
    competitor_brands = {}
    for lead in all_leads:
        outcome = lead.get("purchase_outcome", "")
        if outcome == "lost_to_codealer":
            loss_summary["lost_to_codealer"] += 1
        elif outcome == "lost_to_competitor":
            loss_summary["lost_to_competitor"] += 1
            brand = lead.get("competitor_brand", "Unknown")
            competitor_brands[brand] = competitor_brands.get(brand, 0) + 1

    return {
        "total_calls":        total_calls,
        "calls_today":        calls_today,
        "avg_duration_sec":   avg_duration,
        "avg_duration_min":   round(avg_duration / 60, 1) if avg_duration else 0,
        "sentiment":          sentiment,
        "hourly_activity":    hourly,
        "salesperson_stats":  list(salesperson_stats.values()),
        "model_interest":     model_interest,
        "loss_summary":       loss_summary,
        "competitor_brands":  competitor_brands,
    }