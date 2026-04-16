"""
lead_manager.py
Business logic: post-call processing, lead scoring, salesperson assignment.
"""
from datetime import datetime, timedelta
import itertools
import config
import sheets_manager as db
from exotel_client import notify_salesperson

# Round-robin assignment tracker
_assignment_counter = itertools.cycle(range(max(len(config.SALES_TEAM), 1)))


def process_call_result(lead_id: str, analysis: dict, transcript: str, duration_sec: int, direction: str = "outbound"):
    """
    After every call ends:
    1. Update lead status/temperature
    2. Schedule follow-up if needed
    3. Assign to salesperson if hot/converted
    4. Log the call
    """
    lead = db.get_lead_by_id(lead_id) if lead_id else None
    mobile = lead.get("mobile", "") if lead else ""
    
    # ── Map analysis → lead updates ──────────────────────────────────────────
    updates = {}
    temp = analysis.get("temperature", "warm")
    outcome = analysis.get("call_outcome", "interested")
    
    updates["temperature"]  = temp
    updates["last_called"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
    updates["call_count"]   = int(lead.get("call_count", 0)) + 1 if lead else 1
    
    # Only fill name if empty
    if analysis.get("customer_name") and lead and not lead.get("name"):
        updates["name"] = analysis["customer_name"]

    # Always overwrite with latest
    if analysis.get("family_upsell_note"):
        updates["family_info"] = analysis["family_upsell_note"]
    if analysis.get("whatsapp_number"):
        updates["whatsapp"] = analysis["whatsapp_number"]
    if analysis.get("interested_model"):
        updates["interested_model"] = analysis["interested_model"]
    if analysis.get("budget_range"):
        updates["budget"] = analysis["budget_range"]
    if analysis.get("purchase_outcome"):
        updates["purchase_outcome"] = analysis["purchase_outcome"]
    if analysis.get("competitor_brand"):
        updates["competitor_brand"] = analysis["competitor_brand"]
    if analysis.get("loss_reason"):
        updates["loss_reason"] = analysis["loss_reason"]

    # Append notes with timestamp
    if analysis.get("notes"):
        old_notes = lead.get("notes", "") if lead else ""
        call_num = int(lead.get("call_count", 0)) + 1 if lead else 1
        timestamp = datetime.now().strftime("%d %b %H:%M")
        new_note = f"[Call {call_num} - {timestamp}] {analysis['notes']}"
        updates["notes"] = f"{old_notes}\n{new_note}".strip() if old_notes else new_note

    # Append feedback notes
    if analysis.get("feedback_notes"):
        old_feedback = lead.get("feedback_notes", "") if lead else ""
        new_feedback = analysis["feedback_notes"]
        updates["feedback_notes"] = f"{old_feedback}\n{new_feedback}".strip() if old_feedback else new_feedback

    # ── Status transitions ────────────────────────────────────────────────────
    if temp == "dead" or outcome == "not_interested":
        updates["status"] = "dead"
        updates["next_followup"] = ""
    elif analysis.get("purchase_outcome") == "lost_to_codealer":
        updates["status"] = "lost_to_codealer"
        updates["next_followup"] = ""
    elif analysis.get("purchase_outcome") == "lost_to_competitor":
        updates["status"] = "lost_to_competitor"
        updates["next_followup"] = ""
    elif analysis.get("convert_to_sale") or outcome == "converted":
        updates["status"] = "converted"
        updates["converted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        updates["next_followup"] = ""
    elif temp == "hot":
        updates["status"] = "hot"
        updates["next_followup"] = _compute_followup(analysis, hours_default=4)
    else:
        updates["status"] = "active"
        updates["next_followup"] = _compute_followup(analysis)
    
    # ── Max attempts → mark dead ──────────────────────────────────────────────
    if updates.get("call_count", 0) >= config.MAX_FOLLOWUP_ATTEMPTS and temp in ("cold",) and outcome == "no_answer":
        updates["status"] = "dead"
        updates["next_followup"] = ""
    
    # ── Update sheet ──────────────────────────────────────────────────────────
    if lead_id:
        db.update_lead(lead_id, updates)
    
    # ── Log call ──────────────────────────────────────────────────────────────
    db.log_call({
        "lead_id": lead_id or "",
        "mobile": mobile,
        "direction": direction,
        "duration_sec": duration_sec,
        "status": outcome,
        "transcript": transcript[:5000],
        "sentiment": analysis.get("sentiment", "neutral"),
        "ai_summary": analysis.get("notes", ""),
        "next_action": analysis.get("next_action", ""),
    })
    
    # ── Assign salesperson if hot/converted ───────────────────────────────────
    if analysis.get("assign_to_salesperson") or updates.get("status") in ("converted", "hot"):
        _assign_salesperson(lead_id, lead, updates)


def _compute_followup(analysis: dict, hours_default: int = 24) -> str:
    """Calculate next follow-up datetime string."""
    nf = analysis.get("next_followup_date")
    if nf:
        try:
            dt = datetime.strptime(nf, "%Y-%m-%d %H:%M")
            # If Groq returned midnight (00:00), replace with default working hours
            if dt.hour == 0 and dt.minute == 0:
                h, m = config.DEFAULT_FOLLOWUP_TIME.split(":")
                dt = dt.replace(hour=int(h), minute=int(m))
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    
    # Default: next working day at configured time
    h, m = config.DEFAULT_FOLLOWUP_TIME.split(":")
    next_dt = datetime.now() + timedelta(hours=hours_default)
    next_dt = next_dt.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    
    # Skip to Monday if Sunday
    if next_dt.strftime("%A") == "Sunday":
        next_dt += timedelta(days=1)
    
    return next_dt.strftime("%Y-%m-%d %H:%M")


def _assign_salesperson(lead_id: str, lead: dict, updates: dict):
    """Round-robin assign salesperson and notify via SMS."""
    if not config.SALES_TEAM:
        return
    
    sp = config.SALES_TEAM[next(_assignment_counter) % len(config.SALES_TEAM)]
    
    if lead_id:
        db.update_lead(lead_id, {
            "assigned_to": sp["name"],
            "assigned_mobile": sp["mobile"]
        })
    
    merged_lead = {**(lead or {}), **updates, "lead_id": lead_id}
    notify_salesperson(sp, merged_lead)
    print(f"[LeadManager] Lead {lead_id} assigned to {sp['name']} ({sp['mobile']})")


def add_leads_from_import(leads_list: list) -> list:
    """Bulk add leads. Returns list of created lead IDs."""
    ids = []
    for lead in leads_list:
        if not lead.get("mobile"):
            continue
        existing = db.get_lead_by_mobile(lead["mobile"])
        if existing:
            print(f"[LeadManager] Lead with mobile {lead['mobile']} already exists, skipping")
            continue
        lid = db.add_lead(lead)
        ids.append(lid)
    return ids


def get_dashboard_stats() -> dict:
    """Return stats for admin dashboard."""
    all_leads = db.get_all_leads()
    stats = {
        "total": len(all_leads),
        "new": 0, "hot": 0, "warm": 0, "cold": 0,
        "converted": 0, "dead": 0, "active": 0,
        "lost_to_codealer": 0, "lost_to_competitor": 0,
    }
    for l in all_leads:
        s = l.get("status", "new")
        t = l.get("temperature", "warm")
        if s in stats: stats[s] += 1
        elif t in stats: stats[t] += 1
        else: stats["active"] = stats.get("active", 0) + 1
    return stats