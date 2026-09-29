import os
import re
import time
import threading
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ================= AYARLAR =================
BOT_TOKEN = "8980586429:AAHo3dkEiE2Veb7rLYgE-8xWD9h4CANjHgo"
CHAT_ID = "1519060691"  # Kendi sayısal ID'ni buraya yaz

# BGN -> EUR sabit kuru
BGN_TO_EUR = 1.95583

# Bildirim tetikleme eşiği (Euro)
MIN_PROFIT_DEFAULT = 50.0  # Az ve öz fırsatlar için 50 € idealdir, dilediğinde değiştirebilirsin

# Model referans piyasa fiyatları (Euro)
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

# Negatif filtre kelimeleri
EXCLUDE_KEYWORDS = [
    "icloud", "за части", "chasti", "ne raboti", "не работи",
    "povreda", "повреда", "schupen", "счупен", "display", "дисплей",
    "blokiran", "блокиран", "bypass"
]

SEEN_LISTING_IDS = set()

# ================= YARDIMCI FONKSİYONLAR =================
def send_telegram_message(message: str):
    """Telegram üzerinden HTML mesaj gönderir."""
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

def format_deals_message(deals: list) -> str:
    """Birden fazla fırsatı tek ve şık bir mesaj metninde birleştirir."""
    header = f"🎯 <b>YENİ FIRSAT YAKALANDI ({len(deals)} İlan)</b>\n\n"
    items_text = []
    
    for d in deals:
        block = (
            f"📱 <b>{d['model']}</b>\n"
            f"🏷 <i>{d['title']}</i>\n"
            f"💵 <b>Fiyat:</b> {d['price_eur']:.2f} €\n"
            f"📊 <b>Piyasa:</b> ~{d['market_eur']:.2f} €\n"
            f"📈 <b>Net Kâr:</b> <b>+{d['profit_eur']:.2f} €</b>\n"
            f"🔗 <a href='{d['url']}'>İlana Gitmek İçin Tıkla</a>"
        )
        items_text.append(block)
        
    return header + "\n\n──────────────────\n\n".join(items_text)

# ================= SCRAPING VE PARSING =================
def scrape_olx_page(page: int = 1):
    target_url = f"https://www.olx.bg/elektronika/telefoni/iphone/?search%5Border%5D=created_at:desc&page={page}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.olx.bg/"
    }
    
    try:
        resp = cffi_requests.get(target_url, headers=headers, impersonate="chrome120", timeout=15)
        if resp.status_code != 200:
            print(f"[!] OLX yanıt vermedi, durum: {resp.status_code}")
            return []
    except Exception as e:
        print(f"[!] İstek hatası: {e}")
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

            listings.append({
                "id": listing_id,
                "title": title,
                "price_eur": price_eur,
                "url": url
            })
        except Exception:
            continue

    return listings

def evaluate_and_filter(listings, min_profit=MIN_PROFIT_DEFAULT):
    good_deals = []
    for item in listings:
        title_low = item["title"].lower()
        if any(bad_word in title_low for bad_word in EXCLUDE_KEYWORDS):
            continue
            
        model = detect_model(item["title"])
        if not model:
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
                "url": item["url"]
            })
    return good_deals

# ================= TELEGRAM DİNLEYİCİ VE TARAYICI =================
def background_auto_scanner():
    """Her 5 dakikada bir tarar, bulduğu tüm yeni ilanları TEK BİR MESAJDA iletir."""
    while True:
        try:
            items = scrape_olx_page(page=1)
            deals = evaluate_and_filter(items, min_profit=MIN_PROFIT_DEFAULT)
            
            # Sadece daha önce iletilmemiş yeni ilanları seç
            new_deals = []
            for deal in deals:
                if deal["id"] not in SEEN_LISTING_IDS:
                    SEEN_LISTING_IDS.add(deal["id"])
                    new_deals.append(deal)
            
            # Yeni fırsat varsa bekletmeden tek bir mesaj olarak at
            if new_deals:
                single_message = format_deals_message(new_deals)
                send_telegram_message(single_message)
                
        except Exception as e:
            print(f"[!] Otomatik tarama döngü hatası: {e}")
        time.sleep(300)

def telegram_listener():
    """Manuel /tara komutu geldiğinde anlık bulunanları tek mesajda toplar."""
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=30"
            resp = requests.get(url, timeout=35).json()
            if resp.get("ok"):
                for upd in resp.get("result", []):
                    last_update_id = upd["update_id"]
                    msg_obj = upd.get("message", {})
                    text = msg_obj.get("text", "").strip().lower()

                    if text in ["/tara", "tara"]:
                        send_telegram_message("🔍 OLX taranıyor, güncel fırsatlar hesaplanıyor...")
                        items = scrape_olx_page(page=1)
                        deals = evaluate_and_filter(items, min_profit=MIN_PROFIT_DEFAULT)
                        
                        if not deals:
                            send_telegram_message(f"⚠️ Şu anda minimum kâr eşiğini ({MIN_PROFIT_DEFAULT:.0f} €) geçen yeni ilan bulunamadı.")
                        else:
                            # İlk 5 fırsatı tek mesaj kartı olarak gönderir
                            msg = format_deals_message(deals[:5])
                            send_telegram_message(msg)
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
        f"• Yalnızca <b>+{MIN_PROFIT_DEFAULT:.0f} € ve üzeri</b> kâr bırakan ilanlar taranır.\n"
        "• Yeni fırsatlar tek bir özet bildirim kartında iletilir.\n"
        "• Dilediğin zaman <b>/tara</b> yazabilirsin."
    )

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
