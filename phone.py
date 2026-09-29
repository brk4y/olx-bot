import os
import re
import time
import threading
from urllib.parse import quote
from curl_cffi import requests
from bs4 import BeautifulSoup

# ==========================================
# ⚙️ AYARLAR VE TELEGRAM YAPILANDIRMASI
# ==========================================
BOT_TOKEN = "8980586429:AAHo3dkEiE2Veb7rLYgE-8xWD9h4CANjHgo"
CHAT_ID = "1519060691"  # Telegram sayısal ID'niz 

MIN_PROFIT_DEFAULT = 60.0  # Bildirim için minimum kâr eşiği (€)
BGN_TO_EUR_RATE = 1.95583  # Bulgar Levası -> Euro sabit kur

# iPhone 13 Pro Max piyasa referans fiyatı (€)
BASE_MARKET_MEDIAN = 420.0

# Hafıza bazlı piyasa çarpanları
STORAGE_FACTORS = {
    "128": 1.0,
    "256": 1.10,
    "512": 1.22,
    "1tb": 1.35
}

# ==========================================
# 📩 TELEGRAM İLETİŞİM FONKSİYONLARI
# ==========================================
def send_telegram_message(message: str):
    """Kullanıcıya HTML formatında Telegram bildirimi gönderir."""
    if not CHAT_ID or CHAT_ID == "BURAYA_USERINFO_ID_YAZ":
        print("[!] Hata: CHAT_ID tanımlı değil!")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code != 200:
            print(f"[!] Telegram mesaj hatası: {res.text}")
    except Exception as e:
        print(f"[!] Telegram bildirim hatası: {e}")

# ==========================================
# 🔍 METİN ANALİZİ VE FİYAT ÇÖZÜMLEME
# ==========================================
def extract_battery_health(text: str):
    """Açıklama ve başlıktan pil sağlığı yüzdesini tespit eder."""
    matches = re.findall(r"(?:battery|батерия|живот|капацитет|health)?\s*[:\-\s]?\s*(\d{2,3})\s*%", text, re.I)
    valid = [int(m) for m in matches if 60 <= int(m) <= 100]
    return valid[0] if valid else None

def extract_storage(text: str):
    """Metin içinden hafıza kapasitesini tespit eder."""
    m = re.search(r"\b(128|256|512|1024)\s*(?:gb|гб)?\b|\b(1\s*tb|1\s*тб)\b", text, re.I)
    if m:
        val = m.group(1) or m.group(2)
        val = val.lower().replace(" ", "").replace("гб", "gb").replace("тб", "tb")
        if val in ["1024", "1024gb", "1tb"]:
            return "1tb"
        return val.replace("gb", "")
    return "128"

def parse_price_eur(raw_text: str):
    """OLX üzerindeki fiyat metnini Euro para birimine dönüştürür."""
    if not raw_text:
        return None
    cleaned = raw_text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not m:
        return None
    amount = float(m.group(1))

    # İlan para birimini kontrol et (лв veya €)
    if "лв" in raw_text.lower() or "bgn" in raw_text.lower():
        return round(amount / BGN_TO_EUR_RATE, 2)
    elif "€" in raw_text or "eur" in raw_text.lower():
        return round(amount, 2)
    else:
        # Fiyat büyükse leva kabul edip Euro'ya çevir
        if amount > 500:
            return round(amount / BGN_TO_EUR_RATE, 2)
        return round(amount, 2)

# ==========================================
# 🌐 OLX.BG KAZIMA VE FIRSAT HESAPLAMA
# ==========================================
def fetch_and_evaluate_deals():
    """OLX.bg ilanlarını çeker, filtreler ve kâr potansiyelini hesaplar."""
    search_query = quote("iphone 13 pro max")
    url = f"https://www.olx.bg/elektronika/telefoni/smartfoni/q-{search_query}/?search%5Border%5D=created_at:desc"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        response = requests.get(url, headers=headers, impersonate="chrome120", timeout=25)
        if response.status_code != 200:
            print(f"[!] OLX yanıt vermedi, durum: {response.status_code}")
            return []
    except Exception as e:
        print(f"[!] OLX bağlantı hatası: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select('div[data-cy="l-card"]')
    deals = []

    for card in cards:
        try:
            # Başlık ve URL
            title_el = card.select_one("h6") or card.select_one("h4")
            link_el = card.select_one("a")
            if not title_el or not link_el:
                continue

            title = title_el.get_text(strip=True)
            href = link_el.get("href", "")
            ad_url = href if href.startswith("http") else f"https://www.olx.bg{href}"

            # Negatif filtre (Kılıf, cam, replika vb. temizliği)
            negative_keywords = ["case", "калъф", "кейс", "протектор", "display", "дисплей", "части", "icloude", "за части"]
            if any(nw in title.lower() for nw in negative_keywords):
                continue

            # Fiyat
            price_el = card.select_one('p[data-testid="ad-price"]')
            if not price_el:
                continue
            price_eur = parse_price_eur(price_el.get_text(strip=True))
            if not price_eur or price_eur < 150.0:  # Aksesuar veya hatalı fiyatları ele
                continue

            # Hafıza ve Pil
            storage = extract_storage(title)
            battery = extract_battery_health(title)

            # Piyasa Medyanı ve Kâr Hesabı
            storage_mult = STORAGE_FACTORS.get(storage, 1.0)
            market_value_eur = BASE_MARKET_MEDIAN * storage_mult

            # Pil sağlığına göre değer düzeltmesi
            if battery and battery < 80:
                market_value_eur -= 40.0  # Pil değişimi gerektiren durum indirimi

            estimated_profit = market_value_eur - price_eur

            # Konum
            location_el = card.select_one('p[data-testid="location-date"]')
            location = location_el.get_text(strip=True).split(" - ")[0] if location_el else "Bilinmiyor"

            deals.append({
                "title": title,
                "url": ad_url,
                "price": price_eur,
                "market_value": round(market_value_eur, 2),
                "profit": round(estimated_profit, 2),
                "storage": storage,
                "battery": battery,
                "location": location
            })
        except Exception:
            continue

    # Kâra göre en yükseği öne al
    deals.sort(key=lambda x: x["profit"], reverse=True)
    return deals

# ==========================================
# 🤖 TELEGRAM KOMUT DİNLEYİCİSİ (/tara)
# ==========================================
def telegram_listener():
    """Telegram üzerinden gelen /tara komutlarını dinler."""
    offset = 0
    print("[*] Telegram komut dinleyicisi devrede...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=20"
            res = requests.get(url, timeout=25)
            if res.status_code == 200:
                data = res.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message", {})
                        text = msg.get("text", "").strip().lower()

                        if text in ["/tara", "tara"]:
                            send_telegram_message("🔎 <b>OLX taranıyor, güncel fırsatlar hesaplanıyor...</b>")
                            deals = fetch_and_evaluate_deals()
                            profitable = [d for d in deals if d["profit"] >= MIN_PROFIT_DEFAULT]

                            if not profitable:
                                send_telegram_message("⚠️ Şu anda minimum kâr eşiğini (60 €) geçen yeni ilan bulunamadı.")
                                continue

                            report = "🎯 <b>GÜNCEL IPHONE 13 PRO MAX FIRSATLARI:</b>\n\n"
                            for d in profitable[:5]:
                                bat_info = f"%{d['battery']}" if d['battery'] else "Belirtilmemiş"
                                report += (
                                    f"📱 <b>{d['title']}</b>\n"
                                    f"💰 İlan Fiyatı: <b>{d['price']:.0f} €</b>\n"
                                    f"📈 Piyasa Değeri: ~{d['market_value']:.0f} €\n"
                                    f"💵 Tahmini Net Kâr: <b>+{d['profit']:.0f} €</b>\n"
                                    f"🔋 Pil: {bat_info} | 💾 Hafıza: {d['storage']} GB\n"
                                    f"📍 Konum: {d['location']}\n"
                                    f"🔗 <a href='{d['url']}'>İlana Git</a>\n\n"
                                )
                            send_telegram_message(report)
        except Exception as e:
            time.sleep(5)
        time.sleep(2)

# ==========================================
# ⏰ 5 DAKİKALIK OTOMATİK ARKA PLAN TARAYICI
# ==========================================
seen_ad_urls = set()

def background_auto_scanner():
    """Her 5 dakikada bir sessizce tarar, yeni fırsat yakalarsa anında haber verir."""
    global seen_ad_urls
    print("[*] 5 dakikalık otomatik arka plan tarayıcısı başlatıldı...")

    while True:
        try:
            deals = fetch_and_evaluate_deals()
            new_opportunities = []

            for d in deals:
                if d["url"] not in seen_ad_urls:
                    seen_ad_urls.add(d["url"])
                    if d["profit"] >= MIN_PROFIT_DEFAULT:
                        new_opportunities.append(d)

            # İlk çalıştırmada eski ilanları hafızaya alıp bildirim yağmurunu engelle
            if len(seen_ad_urls) > len(new_opportunities) and new_opportunities:
                for d in new_opportunities:
                    bat_info = f"%{d['battery']}" if d['battery'] else "Belirtilmemiş"
                    alert = (
                        f"🚨 <b>YENİ FIRSAT İLANI DÜŞTÜ!</b>\n\n"
                        f"📱 <b>{d['title']}</b>\n"
                        f"💰 Satış Fiyatı: <b>{d['price']:.0f} €</b>\n"
                        f"📈 Hedef Satış: ~{d['market_value']:.0f} €\n"
                        f"💵 Tahmini Kâr: <b>+{d['profit']:.0f} €</b>\n"
                        f"🔋 Pil: {bat_info} | 💾 {d['storage']} GB\n"
                        f"📍 {d['location']}\n\n"
                        f"🔗 <a href='{d['url']}'>İlanı Aç ve Satıcıya Yaz</a>"
                    )
                    send_telegram_message(alert)
                    time.sleep(1)

        except Exception as e:
            print(f"[!] Otomatik tarayıcı hatası: {e}")

        time.sleep(300)  # 5 dakika bekle

# ==========================================
# 🚀 ANA ÇALIŞTIRMA (BACKGROUND WORKER)
# ==========================================
def main():
    print("[*] iPhone Fırsat Radarı (Worker) Aktif Ediliyor...")

    # 1. Telegram Komut Dinleyicisini Başlat
    listener_thread = threading.Thread(target=telegram_listener, daemon=True)
    listener_thread.start()

    # 2. Otomatik 5 Dakikalık Tarayıcıyı Başlat
    auto_thread = threading.Thread(target=background_auto_scanner, daemon=True)
    auto_thread.start()

    # Başlangıç Bildirimi Gönder
    send_telegram_message(
        "🚀 <b>Bot Render Background Worker Olarak 7/24 Aktif!</b>\n\n"
        "• Sistem her 5 dakikada bir sessizce tarar.\n"
        f"• Yalnızca <b>+{MIN_PROFIT_DEFAULT:.0f} € ve üzeri</b> kâr bırakan yeni ilanlar düştüğünde bildirim atar.\n"
        "• Dilediğin zaman <b>/tara</b> yazarak manuel liste çekebilirsin."
    )

    # Worker sürecini sonsuz döngüde canlı tut
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
