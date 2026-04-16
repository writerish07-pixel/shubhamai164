"""
scraper.py
Scrapes Hero MotoCorp bike models, prices from the dealership website.
Runs once on startup and refreshes every 24 hours.
Also parses uploaded offer files (PDF / Excel / Image).
"""
import os, re, json
import requests
import pdfplumber
import pandas as pd
from PIL import Image
import pytesseract
from bs4 import BeautifulSoup
from pathlib import Path
import config

CACHE_FILE = Path("data/bikes_cache.json")
CACHE_FILE.parent.mkdir(exist_ok=True)


# ── HERO WEBSITE SCRAPER ─────────────────────────────────────────────────────

HERO_MODELS_FALLBACK = [
    {"model": "Splendor Plus", "price_min": 74000, "price_max": 78000, "type": "commuter", "engine": "97.2cc"},
    {"model": "HF Deluxe", "price_min": 67000, "price_max": 71000, "type": "commuter", "engine": "97.2cc"},
    {"model": "Passion Pro", "price_min": 78000, "price_max": 83000, "type": "commuter", "engine": "113cc"},
    {"model": "Glamour", "price_min": 82000, "price_max": 91000, "type": "commuter", "engine": "124.7cc"},
    {"model": "Super Splendor", "price_min": 84000, "price_max": 89000, "type": "commuter", "engine": "124.7cc"},
    {"model": "Destini 125", "price_min": 82000, "price_max": 89000, "type": "scooter", "engine": "124.6cc"},
    {"model": "Maestro Edge 125", "price_min": 85000, "price_max": 95000, "type": "scooter", "engine": "124.6cc"},
    {"model": "Xoom 110", "price_min": 75000, "price_max": 80000, "type": "scooter", "engine": "110cc"},
    {"model": "Xtreme 160R", "price_min": 132000, "price_max": 145000, "type": "sports", "engine": "163cc"},
    {"model": "Xtreme 125R", "price_min": 95000, "price_max": 105000, "type": "sports", "engine": "124.7cc"},
    {"model": "Mavrick 440", "price_min": 194000, "price_max": 205000, "type": "cruiser", "engine": "440cc"},
    {"model": "XPulse 200", "price_min": 145000, "price_max": 160000, "type": "adventure", "engine": "199.6cc"},
    {"model": "XPulse 200T", "price_min": 138000, "price_max": 152000, "type": "adventure", "engine": "199.6cc"},
]


def scrape_hero_website() -> list:
    """Try to scrape live prices from Hero website, fall back to hardcoded list."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(config.WEBSITE_URL, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        bikes = []
        # Try common patterns on Hero dealer pages
        for item in soup.select(".bike-card, .model-card, .product-card, .vehicle-card"):
            name_el  = item.select_one("h2, h3, .model-name, .bike-name")
            price_el = item.select_one(".price, .model-price, .bike-price")
            if name_el:
                model = name_el.get_text(strip=True)
                price_text = price_el.get_text(strip=True) if price_el else ""
                nums = re.findall(r"[\d,]+", price_text.replace(",",""))
                price_min = int(nums[0]) if nums else 0
                price_max = int(nums[-1]) if len(nums) > 1 else price_min
                bikes.append({"model": model, "price_min": price_min, "price_max": price_max,
                              "type": "unknown", "engine": ""})

        if bikes:
            _save_cache(bikes)
            return bikes
    except Exception as e:
        print(f"[Scraper] Website scrape failed: {e}, using fallback data")

    _save_cache(HERO_MODELS_FALLBACK)
    return HERO_MODELS_FALLBACK


def get_bike_catalog() -> list:
    """Return cached catalog or scrape fresh."""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            return data
        except Exception:
            pass
    return scrape_hero_website()


def _save_cache(data: list):
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def format_catalog_for_ai(bikes: list) -> str:
    """Return catalog as a clean text block for the AI system prompt."""
    lines = ["=== HERO MOTOCORP BIKE CATALOG — SHUBHAM MOTORS, JAIPUR ===\n"]
    types = {}
    for b in bikes:
        t = b.get("type", "other")
        types.setdefault(t, []).append(b)
    
    for category, items in types.items():
        lines.append(f"\n[ {category.upper()} ]")
        for b in items:
            p_min = b.get("price_min", 0)
            p_max = b.get("price_max", 0)
            if p_min and p_max and p_min != p_max:
                price_str = f"₹{p_min//1000}K – ₹{p_max//1000}K"
            elif p_min:
                price_str = f"₹{p_min//1000}K onwards"
            else:
                price_str = "Price on request"
            eng = f" | {b['engine']}" if b.get("engine") else ""
            lines.append(f"  • {b['model']}{eng} — {price_str} (ex-showroom Jaipur)")
    
    return "\n".join(lines)


# ── OFFER FILE PARSERS ────────────────────────────────────────────────────────

def parse_offer_file(filepath: str) -> str:
    """Extract text content from PDF, Excel, or Image offer file."""
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".pdf":
            return _parse_pdf(filepath)
        elif ext in (".xlsx", ".xls", ".csv"):
            return _parse_excel(filepath)
        elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            return _parse_image(filepath)
        else:
            return f"Unsupported file type: {ext}"
    except Exception as e:
        return f"Error parsing offer file: {e}"


def _parse_pdf(path: str) -> str:
    text = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text.append(t)
    return "\n".join(text)


def _parse_excel(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    return df.to_string(index=False)


def _parse_image(path: str) -> str:
    img = Image.open(path)
    text = pytesseract.image_to_string(img, lang="eng+hin")
    return text.strip()