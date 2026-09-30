import os
import re
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ================= AYARLAR =================
BOT_TOKEN = "8980586429:AAHo3dkEiE2Veb7rLYgE-8xWD9h4CANjHgo"
CHAT_ID = "1519060691"  # Kendi sayısal ID numaranı yaz

BGN_TO_EUR = 1.95583

# Dinamik Filtre Değişkenleri
MIN_PROFIT_DEFAULT = 50.0   # Minimum net kâr eşiği (€)
MAX_BUDGET = 9999.0         # Maksimum bütçe sınırı (€)
FILTER_MODEL = None         # Spesifik model filtresi (None = hepsi)
MIN_BATTERY = None          # Minimum pil sağlığı (%) (None = filtre kapalı)

BASE_MARKET_PRICES = {
    "iphone 11": 220.0,
    "iphone 11 pro": 270.0,
    "iphone 11 pro max": 320.0,
    "iphone 12": 290.0,
    "iphone 12 mini": 250.0,
    "iphone 12 pro": 360.0,
    "iphone 12 pro max": 420.0,
    "iphone 13": 390.0,
    "iphone 13 mini": 340.0,
    "iphone 13 pro": 480.0,
    "iphone 13 pro max": 550.0,
    "iphone 14": 490.0,
    "iphone 14 plus": 520.0,
    "iphone 14 pro": 630.0,
    "iphone 14 pro max": 720.0,
    "iphone 15": 620.0,
    "iphone 15 plus": 680.0,
    "iphone 15 pro": 820.0,
    "iphone 15 pro max": 930.0,
}

EXCLUDE_KEYWORDS = [
    "icloud", "за части", "chasti", "ne raboti", "не работи",
    "povreda", "повреда", "schupen", "счупен", "display", "дисплей",
    "blokiran", "блокиран", "bypass"
]

SEEN_LISTING_IDS = set()
LAST_SCAN_TIME = "Henüz yapılmadı"

# ================= YARDIMCI FONKSİYONLAR =================
def send_telegram_message(message: str):
    if CHAT_ID == "BURAYA_KENDI_SAYISAL_IDNI_YAZ" or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[!] Telegram bildirim hatası: {e}")

def parse_price_to_eur(raw_text: str) -> float:
    if not raw_text:
        return 0.0
    cleaned = re.sub(r"[^\d,\.]", "", raw_text).replace(",", ".")
    try:
        val = float(cleaned)
        if "€" not in raw_text and ("лв" in raw_text.lower() or "bgn" in raw_text.lower() or "lv" in raw_text.lower() or val > 0):
            return round(val / BGN_TO_EUR, 2)
        return round(val, 2)
    except Exception:
        return 0.0

def detect_model(title: str):
    title_lower = title.lower()
    matched_model = None
    longest_len = 0
    for model_key in BASE_MARKET_PRICES.keys():
        if re.search(r'\b' + re.escape(model_key) + r'\b', title_lower):
            if len(model_key) > longest_len:
                matched_model = model_key
                longest_len = len(model_key)
    return matched_model

def extract_battery_health(text: str):
    """Metin içindeki batarya yüzdesini tespit eder (%85, 85%, bateriq 85 vb.)."""
    patterns = [
        r'%\s*(\d{2,3})',
        r'(\d{2,3})\s*%',
        r'(?:battery|bateriq|bateria)\s*(\d{2,3})'
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 50 <= val <= 100:
                return val
    return None

def format_deals_message(deals: list) -> str:
    header = f"🎯 <b>FIRSATLAR ({len(deals)} İlan)</b>\n\n"
    items_text = []
    for d in deals:
        battery_str = f"\n🔋 <b>Pil:</b> %{d['battery']}" if d.get("battery") else ""
        block = (
            f"📱 <b>{d['model']}</b>\n"
            f"🏷 <i>{d['title']}</i>\n"
            f"💵 <b>Fiyat:</b> {d['price_eur']:.2f} €\n"
            f"📊 <b>Piyasa:</b> ~{d['market_eur']:.2f} €\n"
            f"📈 <b>Kâr:</b> <b>+{d['profit_eur']:.2f} €</b>"
            f"{battery_str}\n"
            f"🔗 <a href='{d['url']}'>İlana Git</a>"
        )
        items_text.append(block)
    return header + "\n\n──────────────────\n\n".join(items_text)

# ================= SCRAPING VE PARSING =================
def scrape_olx_page(page: int = 1):
    global LAST_SCAN_TIME
    target_url = f"https://www.olx.bg/elektronika/telefoni/iphone/?search%5Border%5D=created_at:desc&page={page}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.olx.bg/"
    }
    
    try:
        resp = cffi_requests.get(target_url, headers=headers, impersonate="chrome120", timeout=15)
        LAST_SCAN_TIME = datetime.now(ZoneInfo("Europe/Sofia")).strftime("%H:%M:%S")
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select('div[data-cy="l-card"]')
    if not cards:
        cards = soup.find_all('div', {'data-testid': 'listing-grid'})
        if cards:
            cards = cards[0].find_all('div', recursive=False)

    listings = []
    for card in cards:
        try:
            link_tag = card.select_one('a[href*="/d/ad/"]') or card.find('a', href=True)
            if not link_tag:
                continue
            href = link_tag.get("href", "")
            url = f"https://www.olx.bg{href}" if href.startswith("/") else href
            listing_id = card.get("id") or href.split("-ID")[-1].split(".")[0]
            
            title_tag = card.select_one('h4') or card.select_one('h6') or link_tag.find(['h4', 'h6'])
            title = title_tag.get_text(strip=True) if title_tag else ""
            if not title:
                continue

            price_tag = card.select_one('p[data-testid="ad-price"]') or card.select_one('[data-testid="ad-price"]')
            raw_price = price_tag.get_text(strip=True) if price_tag else ""
            
            price_eur = parse_price_to_eur(raw_price)
            if price_eur <= 0:
                continue

            listings.append({"id": listing_id, "title": title, "price_eur": price_eur, "url": url})
        except Exception:
            continue

    return listings

def evaluate_and_filter(listings, min_profit, max_budget=9999.0, model_filter=None, min_battery=None):
    good_deals = []
    for item in listings:
        title_low = item["title"].lower()
        if any(bad_word in title_low for bad_word in EXCLUDE_KEYWORDS):
            continue
            
        model = detect_model(item["title"])
        if not model:
            continue
            
        # Model kontrolü
        if model_filter and model_filter.lower() not in model.lower():
            continue
            
        # Bütçe kontrolü
        if item["price_eur"] > max_budget:
            continue
            
        # Pil sağlığı kontrolü (Başlıkta belirtilmişse)
        bat_val = extract_battery_health(item["title"])
        if min_battery and bat_val:
            if bat_val < min_battery:
                continue

        market_val = BASE_MARKET_PRICES[model]
        profit = market_val - item["price_eur"]
        if profit >= min_profit:
            good_deals.append({
                "id": item["id"],
                "model": model.upper(),
                "title": item["title"],
                "price_eur": item["price_eur"],
                "market_eur": market_val,
                "profit_eur": profit,
                "battery": bat_val,
                "url": item["url"]
            })
    return good_deals

# ================= ARKA PLAN DÖNGÜSÜ =================
def background_auto_scanner():
    while True:
        try:
            items = scrape_olx_page(page=1)
            deals = evaluate_and_filter(
                items, 
                min_profit=MIN_PROFIT_DEFAULT, 
                max_budget=MAX_BUDGET, 
                model_filter=FILTER_MODEL,
                min_battery=MIN_BATTERY
            )
            new_deals = []
            for deal in deals:
                if deal["id"] not in SEEN_LISTING_IDS:
                    SEEN_LISTING_IDS.add(deal["id"])
                    new_deals.append(deal)
            if new_deals:
                single_message = format_deals_message(new_deals)
                send_telegram_message(single_message)
        except Exception as e:
            print(f"[!] Otomatik tarama hatası: {e}")
        time.sleep(300)

# ================= TELEGRAM KOMUTLARI =================
def telegram_listener():
    global MIN_PROFIT_DEFAULT, MAX_BUDGET, FILTER_MODEL, MIN_BATTERY, SEEN_LISTING_IDS
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=30"
            resp = requests.get(url, timeout=35).json()
            if resp.get("ok"):
                for upd in resp.get("result", []):
                    last_update_id = upd["update_id"]
                    msg_obj = upd.get("message", {})
                    text = msg_obj.get("text", "").strip()
                    lower_text = text.lower()

                    # 1. TARA
                    if lower_text in ["tara", "/tara"]:
                        send_telegram_message("🔍 OLX taranıyor...")
                        items = scrape_olx_page(page=1)
                        deals = evaluate_and_filter(
                            items, 
                            min_profit=MIN_PROFIT_DEFAULT, 
                            max_budget=MAX_BUDGET, 
                            model_filter=FILTER_MODEL,
                            min_battery=MIN_BATTERY
                        )
                        if not deals:
                            send_telegram_message(f"⚠️ Kriterlere uygun (+{MIN_PROFIT_DEFAULT:.0f} € kâr) yeni ilan bulunamadı.")
                        else:
                            msg = format_deals_message(deals[:5])
                            send_telegram_message(msg)

                    # 2. KAR
                    elif lower_text.startswith("kar") or lower_text.startswith("/kar"):
                        parts = text.split()
                        if len(parts) >= 2 and parts[1].replace(".", "", 1).isdigit():
                            MIN_PROFIT_DEFAULT = float(parts[1])
                            send_telegram_message(f"✅ Kâr eşiği: <b>+{MIN_PROFIT_DEFAULT:.0f} €</b>")
                        else:
                            send_telegram_message("⚠️️ Örnek: <code>kar 60</code>")

                    # 3. BUTCE
                    elif lower_text.startswith("butce") or lower_text.startswith("/butce"):
                        parts = text.split()
                        if len(parts) >= 2 and parts[1].replace(".", "", 1).isdigit():
                            MAX_BUDGET = float(parts[1])
                            send_telegram_message(f"✅ Maksimum bütçe: <b>{MAX_BUDGET:.0f} €</b>")
                        else:
                            send_telegram_message("⚠️ Örnek: <code>butce 400</code>")

                    # 4. MODEL
                    elif lower_text.startswith("model") or lower_text.startswith("/model"):
                        parts = text.split(maxsplit=1)
                        if len(parts) == 2:
                            val = parts[1].strip()
                            if val.lower() in ["hepsi", "tum", "iptal", "reset"]:
                                FILTER_MODEL = None
                                send_telegram_message("✅ Model filtresi kaldırıldı. Tüm iPhone'lar taranıyor.")
                            else:
                                FILTER_MODEL = val
                                send_telegram_message(f"✅ Hedef model: <b>iPhone {FILTER_MODEL.upper()}</b>")
                        else:
                            send_telegram_message("⚠️ Örnek: <code>model 13 pro</code> veya <code>model hepsi</code>")

                    # 5. PIL
                    elif lower_text.startswith("pil") or lower_text.startswith("/pil"):
                        parts = text.split()
                        if len(parts) == 2:
                            val = parts[1].strip().lower()
                            if val in ["hepsi", "tum", "iptal", "reset", "0"]:
                                MIN_BATTERY = None
                                send_telegram_message("✅ Pil sağlığı filtresi kaldırıldı.")
                            elif val.isdigit():
                                MIN_BATTERY = int(val)
                                send_telegram_message(f"✅ Minimum pil sağlığı: <b>%{MIN_BATTERY}</b>")
                            else:
                                send_telegram_message("⚠️ Örnek: <code>pil 85</code> veya <code>pil hepsi</code>")
                        else:
                            send_telegram_message("⚠️ Örnek: <code>pil 85</code> veya <code>pil hepsi</code>")

                    # 6. DURUM
                    elif lower_text in ["durum", "/durum"]:
                        model_str = f"iPhone {FILTER_MODEL.upper()}" if FILTER_MODEL else "Tüm Modeller"
                        budget_str = f"{MAX_BUDGET:.0f} €" if MAX_BUDGET < 9000 else "Sınırsız"
                        bat_str = f"%{MIN_BATTERY} ve üzeri" if MIN_BATTERY else "Filtresiz"
                        status_msg = (
                            "⚙️ <b>Güncel Durum & Ayarlar</b>\n\n"
                            f"• <b>Kâr Eşiği:</b> +{MIN_PROFIT_DEFAULT:.0f} €\n"
                            f"• <b>Bütçe Limiti:</b> {budget_str}\n"
                            f"• <b>Filtrelenen Model:</b> {model_str}\n"
                            f"• <b>Pil Kriteri:</b> {bat_str}\n"
                            f"• <b>Hafızadaki İlan:</b> {len(SEEN_LISTING_IDS)} adet\n"
                            f"• <b>Son Tarama Saati:</b> {LAST_SCAN_TIME}\n"
                            f"• <b>Sistem:</b> 7/24 Aktif"
                        )
                        send_telegram_message(status_msg)

                    # 7. SIFIRLA
                    elif lower_text in ["sifirla", "/sifirla"]:
                        MIN_PROFIT_DEFAULT = 50.0
                        MAX_BUDGET = 9999.0
                        FILTER_MODEL = None
                        MIN_BATTERY = None
                        SEEN_LISTING_IDS.clear()
                        send_telegram_message("🔄 <b>Tüm ayarlar ve ilan hafızası sıfırlandı!</b>\n(Kâr: +50 €, Bütçe: Sınırsız, Model: Hepsi, Pil: Filtresiz)")

                    # 8. YARDIM
                    elif lower_text in ["yardim", "/yardim", "?", "/help", "/start"]:
                        help_text = (
                            "🤖 <b>Kullanabileceğin Komutlar:</b>\n\n"
                            "• <b>tara</b> : Fırsatları tek mesajda listeler\n"
                            "• <b>kar 60</b> : Minimum kâr eşiğini ayarlar\n"
                            "• <b>butce 350</b> : Maksimum cihaz fiyatını sınırlar\n"
                            "• <b>model 13</b> : Sadece belirtilen modeli arar (kaldırmak için: <code>model hepsi</code>)\n"
                            "• <b>pil 85</b> : Minimum pil sağlığını ayarlar (kaldırmak için: <code>pil hepsi</code>)\n"
                            "• <b>durum</b> : Aktif ayarları ve son tarama saatini gösterir\n"
                            "• <b>sifirla</b> : Tüm ayarları ve hafızayı sıfırlar\n"
                            "• <b>yardim</b> : Bu rehberi açar"
                        )
                        send_telegram_message(help_text)

        except Exception as e:
            print(f"[!] Listener hatası: {e}")
        time.sleep(2)

def main():
    print("[*] iPhone Fırsat Radarı Aktif...")
    listener_thread = threading.Thread(target=telegram_listener, daemon=True)
    listener_thread.start()

    auto_thread = threading.Thread(target=background_auto_scanner, daemon=True)
    auto_thread.start()

    send_telegram_message(
        "🚀 <b>iPhone Fırsat Radarı Devrede!</b>\n\n"
        f"• Kâr Eşiği: <b>+{MIN_PROFIT_DEFAULT:.0f} €</b>\n"
        "• Komutları görmek için <b>yardim</b> yazabilirsin."
    )

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
