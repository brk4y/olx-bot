import os
import re
import time
import threading
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# ================= AYARLAR =================
# Telegram bilgileri
BOT_TOKEN = "8980586429:AAHo3dkEiE2Veb7rLYgE-8xWD9h4CANjHgo"
# Kendi sayısal Telegram Chat ID'ni buraya yaz:
CHAT_ID = "1519060691"

# Para birimi çevirici (BGN -> EUR sabit kuru)
BGN_TO_EUR = 1.95583

# Bildirim tetikleme eşiği (Euro cinsinden)
MIN_PROFIT_DEFAULT = 10.0  # Test için 10 € yapıldı, dilediğinde 50-60 yapabilirsin

# Model referans taban piyasa fiyatları (Euro)
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

# Negatif kelimeler (Arızalı, parçalık, kilitli ilanları elemek için)
EXCLUDE_KEYWORDS = [
    "icloud", "за части", "chasti", "ne raboti", "не работи",
    "povreda", "повреда", "schupen", "счупен", "display", "дисплей",
    "blokiran", "блокиран", "matrichno", "bypass", "otkluchvane"
]

# Zaten görülen ve bildirilen ilanların ID havuzu
SEEN_LISTING_IDS = set()

# ================= YARDIMCI FONKSİYONLAR =================
def send_telegram_message(message: str):
    """Telegram üzerinden HTML formatında bildirim gönderir."""
    if CHAT_ID == "BURAYA_KENDI_SAYISAL_IDNI_YAZ" or not CHAT_ID:
        print("[!] Lütfen geçerli bir CHAT_ID girin.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        res_json = res.json()
        if not res_json.get("ok"):
            print(f"[!] Telegram mesaj hatası: {res.text}")
    except Exception as e:
        print(f"[!] Telegram bağlantı hatası: {e}")

def parse_price_to_eur(raw_text: str) -> float:
    """Fiyat metnini temizleyip Euro'ya çevirir."""
    if not raw_text:
        return 0.0
    cleaned = re.sub(r"[^\d,\.]", "", raw_text).replace(",", ".")
    try:
        val = float(cleaned)
        # Eğer metinde лв / lv geçiyorsa veya Euro belirtilmemişse BGN kabul edip EUR'a çevir
        if "€" not in raw_text and ("лв" in raw_text.lower() or "bgn" in raw_text.lower() or "lv" in raw_text.lower() or val > 0):
            return round(val / BGN_TO_EUR, 2)
        return round(val, 2)
    except Exception:
        return 0.0

def detect_model(title: str):
    """Başlıktan iPhone modelini tespit eder."""
    title_lower = title.lower()
    matched_model = None
    longest_len = 0
    for model_key in BASE_MARKET_PRICES.keys():
        if re.search(r'\b' + re.escape(model_key) + r'\b', title_lower):
            if len(model_key) > longest_len:
                matched_model = model_key
                longest_len = len(model_key)
    return matched_model

# ================= SCRAPING VE PARSING =================
def scrape_olx_page(page: int = 1):
    """OLX Bulgaristan Apple kategorisini 404 almayacak temiz URL ile tarar."""
    # Kesin çalışan temiz kategori linki:
    target_url = f"https://www.olx.bg/elektronika/telefoni/iphone/?search%5Border%5D=created_at:desc&page={page}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.olx.bg/"
    }
    
    try:
        # TLS parmak izi korumasını aşmak için curl_cffi kullanıyoruz
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
        # Alternatif ilan kartı seçici
        cards = soup.find_all('div', {'data-testid': 'listing-grid'})
        if cards:
            cards = cards[0].find_all('div', recursive=False)
            
    print(f"[*] Toplam {len(cards)} adet ilan kartı bulundu.")

    listings = []
    for card in cards:
        try:
            link_tag = card.select_one('a[href*="/d/ad/"]') or card.find('a', href=True)
            if not link_tag:
                continue
            
            href = link_tag.get("href", "")
            url = f"https://www.olx.bg{href}" if href.startswith("/") else href
            
            # İlan ID tespiti
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
    """İlanları model, negatif kelime ve kâr potansiyeline göre süzer."""
    good_deals = []
    for item in listings:
        title_low = item["title"].lower()
        
        # 1. Negatif kelime filtresi
        if any(bad_word in title_low for bad_word in EXCLUDE_KEYWORDS):
            continue
            
        # 2. Model tespiti
        model = detect_model(item["title"])
        if not model:
            continue
            
        market_val = BASE_MARKET_PRICES[model]
        profit = market_val - item["price_eur"]
        
        # 3. Kâr eşiği kontrolü
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
    """Her 5 dakikada bir arka planda sessizce yeni fırsat taraması yapar."""
    while True:
        try:
            items = scrape_olx_page(page=1)
            deals = evaluate_and_filter(items, min_profit=MIN_PROFIT_DEFAULT)
            for deal in deals:
                if deal["id"] not in SEEN_LISTING_IDS:
                    SEEN_LISTING_IDS.add(deal["id"])
                    msg = (
                        f"🚨 <b>YENİ KELEPİR FIRSAT!</b>\n\n"
                        f"📱 <b>Model:</b> {deal['model']}\n"
                        f"🏷 <b>Başlık:</b> {deal['title']}\n"
                        f"💰 <b>Fiyat:</b> {deal['price_eur']:.2f} €\n"
                        f"📊 <b>Piyasa Medyanı:</b> ~{deal['market_eur']:.2f} €\n"
                        f"📈 <b>Tahmini Net Kâr:</b> +{deal['profit_eur']:.2f} €\n\n"
                        f"🔗 <a href='{deal['url']}'>İlana Gitmek İçin Tıkla</a>"
                    )
                    send_telegram_message(msg)
        except Exception as e:
            print(f"[!] Otomatik tarama döngü hatası: {e}")
        time.sleep(300)

def telegram_listener():
    """Telegram üzerinden gelen /tara komutunu dinler."""
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
                            for d in deals[:5]:
                                card_msg = (
                                    f"✨ <b>{d['model']} Fırsatı</b>\n"
                                    f"🏷 <i>{d['title']}</i>\n"
                                    f"💵 <b>Fiyat:</b> {d['price_eur']:.2f} €\n"
                                    f"📈 <b>Beklenen Kâr:</b> +{d['profit_eur']:.2f} €\n"
                                    f"🔗 <a href='{d['url']}'>İlanı Görüntüle</a>"
                                )
                                send_telegram_message(card_msg)
        except Exception as e:
            print(f"[!] Listener hatası: {e}")
        time.sleep(2)

def main():
    print("[*] iPhone Fırsat Radarı (Worker) Aktif Ediliyor...")
    
    # 1. Telegram komut dinleyicisi
    listener_thread = threading.Thread(target=telegram_listener, daemon=True)
    listener_thread.start()
    print("[*] Telegram komut dinleyicisi devrede...")

    # 2. Otomatik tarayıcı
    auto_thread = threading.Thread(target=background_auto_scanner, daemon=True)
    auto_thread.start()
    print("[*] 5 dakikalık otomatik arka plan tarayıcısı başlatıldı...")

    send_telegram_message(
        "🚀 <b>Bot Render Background Worker Olarak 7/24 Aktif!</b>\n\n"
        "• Sistem her 5 dakikada bir sessizce tarar.\n"
        f"• Yalnızca <b>+{MIN_PROFIT_DEFAULT:.0f} € ve üzeri</b> kâr bırakan yeni ilanlar düştüğünde bildirim atar.\n"
        "• Dilediğin zaman <b>/tara</b> yazarak anlık liste çekebilirsin."
    )

    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
