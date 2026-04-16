"""
exotel_client.py
All Exotel API calls: make outbound calls, get call status, send SMS.
Includes retry logic with exponential backoff and connection stability features.
"""
import logging
import time

import requests

import config

log = logging.getLogger("shubham-ai.exotel")

# ── CONNECTION STABILITY HELPERS ──────────────────────────────────────────────

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2  # seconds; doubles each retry


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """
    Execute an HTTP request with exponential backoff retry on transient errors.
    Raises the last exception if all retries are exhausted.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            wait = _RETRY_BACKOFF_BASE ** attempt
            log.warning(
                "Transient error (attempt %d/%d): %s -- retrying in %ds",
                attempt + 1, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
        except requests.HTTPError:
            # 4xx errors are not transient -- don't retry
            raise
    raise last_exc  # type: ignore[misc]


def check_connection() -> bool:
    """
    Heartbeat check: verify Exotel API is reachable.
    Returns True if connection is healthy.
    """
    if not config.EXOTEL_API_KEY or not config.EXOTEL_API_TOKEN:
        log.warning("Exotel credentials not configured, skipping heartbeat")
        return False
    url = f"https://{config.EXOTEL_SUBDOMAIN}/v1/Accounts/{config.EXOTEL_ACCOUNT_SID}"
    try:
        _request_with_retry(
            "GET", url, timeout=10,
            auth=(config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN),
        )
        return True
    except Exception as e:
        log.warning("Heartbeat failed: %s", e)
        return False


def make_outbound_call(to_number: str, lead_id: str = "") -> dict:
    """
    Initiate outbound call from Exophone to customer.
    Exotel will call the customer and bridge to our webhook for AI handling.
    Uses retry logic with exponential backoff for stable connections.
    """
    if not config.EXOTEL_API_KEY or not config.EXOTEL_API_TOKEN:
        log.error("Cannot make call -- Exotel credentials not configured")
        return {"success": False, "error": "Exotel credentials not configured"}
    url = f"https://{config.EXOTEL_SUBDOMAIN}/v1/Accounts/{config.EXOTEL_ACCOUNT_SID}/Calls/connect.json"
    
    # Exotel passthru URL — our app handles the call logic via webhook
    call_handler_url = f"{config.PUBLIC_URL}/call/handler"
    
    payload = {
        "From": to_number,
        "CallerId": config.EXOTEL_PHONE_NUMBER,
        "Url": f"http://my.exotel.com/{config.EXOTEL_ACCOUNT_SID}/exoml/start_voice/{config.EXOTEL_APP_ID}",
        "Record": "true",
        "TimeLimit": 300,          # max 5 min call
        "TimeOut": 30,             # ring timeout
        "StatusCallback": f"{config.PUBLIC_URL}/call/status",
        "CustomField": lead_id,
    }
    
    try:
        r = _request_with_retry(
            "POST", url, data=payload, timeout=15,
            auth=(config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN),
        )
        data = r.json()
        call_sid = data.get("Call", {}).get("Sid", "")
        log.info("Outbound call initiated to %s | SID: %s", to_number, call_sid)
        return {"success": True, "call_sid": call_sid, "data": data}
    except Exception as e:
        log.error("Call failed to %s: %s", to_number, e)
        return {"success": False, "error": str(e)}


def send_sms(to_number: str, message: str) -> dict:
    """Send SMS via Exotel with retry on transient errors."""
    url = f"https://{config.EXOTEL_SUBDOMAIN}/v1/Accounts/{config.EXOTEL_ACCOUNT_SID}/Sms/send.json"
    
    payload = {
        "From": config.EXOTEL_PHONE_NUMBER,
        "To": to_number,
        "Body": message,
    }
    
    try:
        _request_with_retry(
            "POST", url, data=payload, timeout=10,
            auth=(config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN),
        )
        log.info("SMS sent to %s", to_number)
        return {"success": True}
    except Exception as e:
        log.error("SMS failed to %s: %s", to_number, e)
        return {"success": False, "error": str(e)}


def get_call_details(call_sid: str) -> dict:
    """Fetch call details from Exotel."""
    url = f"https://{config.EXOTEL_SUBDOMAIN}/v1/Accounts/{config.EXOTEL_ACCOUNT_SID}/Calls/{call_sid}"
    try:
        r = _request_with_retry(
            "GET", url, timeout=10,
            auth=(config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN),
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def notify_salesperson(salesperson: dict, lead: dict) -> bool:
    """
    Notify a salesperson via SMS when a lead is converted/hot.
    """
    lead_name    = lead.get("name", "Customer")
    lead_mobile  = lead.get("mobile", "")
    lead_model   = lead.get("interested_model", "Hero Bike")
    lead_notes   = lead.get("notes", "")
    
    message = (
        f"🔥 HOT LEAD ASSIGNED!\n"
        f"Hi {salesperson['name']},\n"
        f"Lead: {lead_name}\n"
        f"Mobile: {lead_mobile}\n"
        f"Interest: {lead_model}\n"
        f"Notes: {lead_notes[:100]}\n"
        f"Please call them ASAP!\n"
        f"- Shubham Motors AI"
    )
    
    result = send_sms(salesperson["mobile"], message)
    return result.get("success", False)


# ── HUMAN AGENT TRANSFER ───────────────────────────────────────────────────────

# def transfer_to_human(call_sid: str, agent_number: str = None) -> dict:
#     """
#     Transfer an ongoing call to a human agent.
#     Uses Exotel's 'Transfer' API to bridge the call.
    
#     Args:
#         call_sid: The current call SID to transfer
#         agent_number: Target agent's phone number. If None, uses PRIMARY_AGENT_NUMBER
    
#     Returns:
#         dict with success status and transfer details
#     """
#     import config
    
#     if not agent_number:
#         agent_number = config.PRIMARY_AGENT_NUMBER
    
#     if not agent_number:
#         log.error("No agent number configured for transfer")
#         return {"success": False, "error": "No agent number configured"}
    
#     if not config.EXOTEL_API_KEY or not config.EXOTEL_API_TOKEN:
#         log.error("Cannot transfer -- Exotel credentials not configured")
#         return {"success": False, "error": "Exotel credentials not configured"}
    
#     url = f"https://{config.EXOTEL_SUBDOMAIN}/v1/Accounts/{config.EXOTEL_ACCOUNT_SID}/Calls/{call_sid}/transfer"
    
#     payload = {
#         "PhoneNumber": agent_number,
#         "CallerId": config.EXOTEL_PHONE_NUMBER,
#     }
    
#     try:
#         r = _request_with_retry(
#             "POST", url, data=payload, timeout=15,
#             auth=(config.EXOTEL_API_KEY, config.EXOTEL_API_TOKEN),
#         )
#         data = r.json()
#         log.info("Call %s transferred to agent %s", call_sid, agent_number)
#         return {"success": True, "agent_number": agent_number, "data": data}
#     except Exception as e:
#         log.error("Transfer failed for call %s: %s", call_sid, e)
#         return {"success": False, "error": str(e)}


# def get_available_agent() -> dict:
#     """
#     Get an available agent for transfer.
#     Uses round-robin from AGENT_NUMBERS or falls back to PRIMARY_AGENT_NUMBER.
    
#     Returns:
#         dict with agent number and name
#     """
#     import config
    
#     # Use configured agents list with round-robin
#     if config.AGENT_NUMBERS:
#         import itertools
#         _agent_cycle = itertools.cycle(config.AGENT_NUMBERS)
#         agent = next(_agent_cycle)
#         return agent
    
#     # Fallback to primary agent
#     if config.PRIMARY_AGENT_NUMBER:
#         return {
#             "number": config.PRIMARY_AGENT_NUMBER,
#             "name": config.PRIMARY_AGENT_NAME
#         }
    
#     return {"number": "", "name": "No Agent"}
