import os
import re
import json
import time
import threading
import statistics
import http.server
import socketserver
from curl_cffi import requests

# ================= TELEGRAM AYARLARI =================
BOT_TOKEN = "8980586429:AAHo3dkEiE2Veb7rLYgE-8xWD9h4CANjHgo"
CHAT_ID = "1519060691"  # Kendi Chat ID numaranı buraya yaz

SCAN_LIMIT = 80              # Taranacak ilan adedi
CHECK_INTERVAL_SEC = 300     # 5 dakikada bir kontrol eder (300 saniye)
MIN_PROFIT_DEFAULT = 60.0    # Minimum net kâr eşiği (€)
DISCOUNT_THRESHOLD = 0.12    # Piyasa medyanının en az %12 altında olmalı
DB_FILE = "seen_ads.json"    # İlan takip hafızası
# =====================================================

EUR_BGN_RATE = 1.95583
LAST_UPDATE_ID = 0

USER_FILTER = {
    "target_model": None,
    "max_budget_eur": None,
    "min_profit_eur": MIN_PROFIT_DEFAULT,
    "auto_scan": True        # Bot açıldığında otomatik radar devrededir
}

EXCLUDE_TERMS = [
    "калъф", "калъфи", "кейс", "кейсове", "case", "cover", "гръбче", "гръб",
    "протектор", "стъкло", "стъклен", "за части", "части", "icloud", "айклауд",
    "заключен", "счупен", "спукан", "дисплей", "кутия", "капак", "батерия", 
    "камера", "панел", "за ремонт", "дефект", "без face id", "face id не работи", "реплика"
]

MODEL_CONFIG = {
    "iPhone 17 Pro Max": {"min": 1250, "max_cap": 1600},
    "iPhone 17 Pro":     {"min": 1100, "max_cap": 1400},
    "iPhone 17":         {"min": 850,  "max_cap": 1100},

    "iPhone 16 Pro Max": {"min": 850,  "max_cap": 1200},
    "iPhone 16 Pro":     {"min": 720,  "max_cap": 1000},
    "iPhone 16 Plus":    {"min": 600,  "max_cap": 850},
    "iPhone 16":         {"min": 520,  "max_cap": 750},
    
    "iPhone 15 Pro Max": {"min": 680,  "max_cap": 950},
    "iPhone 15 Pro":     {"min": 560,  "max_cap": 800},
    "iPhone 15 Plus":    {"min": 480,  "max_cap": 650},
    "iPhone 15":         {"min": 430,  "max_cap": 600},

    "iPhone 14 Pro Max": {"min": 520,  "max_cap": 750},
    "iPhone 14 Pro":     {"min": 450,  "max_cap": 650},
    "iPhone 14 Plus":    {"min": 360,  "max_cap": 500},
    "iPhone 14":         {"min": 330,  "max_cap": 480},

    "iPhone 13 Pro Max": {"min": 420,  "max_cap": 600},
    "iPhone 13 Pro":     {"min": 350,  "max_cap": 500},
    "iPhone 13 mini":    {"min": 240,  "max_cap": 350},
    "iPhone 13":         {"min": 280,  "max_cap": 400},

    "iPhone 12 Pro Max": {"min": 290,  "max_cap": 420},
    "iPhone 12 Pro":     {"min": 240,  "max_cap": 360},
    "iPhone 12 mini":    {"min": 150,  "max_cap": 220},
    "iPhone 12":         {"min": 180,  "max_cap": 260},

    "iPhone 11 Pro Max": {"min": 210,  "max_cap": 300},
    "iPhone 11 Pro":     {"min": 170,  "max_cap": 250},
    "iPhone 11":         {"min": 130,  "max_cap": 200},
}


def load_seen_ads() -> set:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_ads(seen_set: set):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] DB Kayıt hatası: {e}")


def send_telegram_message(message: str):
    if CHAT_ID == "BURAYA_USERINFO_ID_YAZ":
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[!] Telegram hatası: {e}")


def detect_model(title: str):
    t = title.lower()
    # Hafıza birimlerini sil (128gb veya 512gb içindeki 12'yi model sanmasın)
    t = re.sub(r"\b\d+\s*(?:gb|гб|tb|тб)\b", " ", t)

    match = re.search(r"(?:iphone|айфон)\s*(1[1-7])\b", t)
    if not match:
        return None, None, None

    series = match.group(1)

    if "pro max" in t:
        name = f"iPhone {series} Pro Max"
    elif "pro" in t:
        name = f"iPhone {series} Pro"
    elif "mini" in t:
        name = f"iPhone {series} mini"
    elif "plus" in t:
        name = f"iPhone {series} Plus"
    else:
        name = f"iPhone {series}"

    if name in MODEL_CONFIG:
        return name, MODEL_CONFIG[name]["min"], MODEL_CONFIG[name]["max_cap"]

    return None, None, None


def scrape_olx_api(total_items=80, query_filter=None):
    all_listings = []
    seen_links = set()
    session = requests.Session(impersonate="chrome124")

    search_text = f"iphone {query_filter}" if query_filter else "iphone"
    offset = 0
    limit = 40

    while offset < total_items:
        api_url = (
            f"https://www.olx.bg/api/v1/offers/?"
            f"offset={offset}&limit={limit}&query={requests.utils.quote(search_text)}&"
            f"sort_by=created_at:desc"
        )
        try:
            res = session.get(api_url, timeout=15)
            if res.status_code != 200:
                break
            data = res.json()
            offers = data.get("data", [])
            if not offers:
                break
        except Exception:
            break

        for item in offers:
            title = item.get("title", "")
            url = item.get("url", "")
            params = item.get("params", [])

            price_eur = None
            for p in params:
                if p.get("key") == "price":
                    price_val = p.get("value", {})
                    raw_val = price_val.get("value")
                    currency = price_val.get("currency", "BGN")
                    if raw_val is not None:
                        try:
                            val = float(raw_val)
                            price_eur = round(val / EUR_BGN_RATE, 2) if currency in ["BGN", "лв"] else round(val, 2)
                        except ValueError:
                            pass
                    break

            if not price_eur or price_eur < 100 or url in seen_links:
                continue

            title_lower = title.lower()
            if any(term in title_lower for term in EXCLUDE_TERMS):
                continue

            model, normal_market_min, max_cap = detect_model(title)
            if not model:
                continue

            seen_links.add(url)
            all_listings.append({
                "model": model,
                "title": title,
                "price_eur": price_eur,
                "link": url,
                "market_min": normal_market_min,
                "max_cap": max_cap
            })

        offset += limit
        time.sleep(0.3)

    return all_listings


def analyze_and_find_deals(listings, discount_threshold=0.12):
    grouped = {}
    for item in listings:
        grouped.setdefault(item["model"], []).append(item)

    profitable_deals = []

    for model, items in grouped.items():
        base_min = items[0]["market_min"]
        max_cap = items[0]["max_cap"]

        valid_prices = [
            x["price_eur"] for x in items 
            if base_min * 0.7 <= x["price_eur"] <= max_cap
        ]

        if len(valid_prices) >= 2:
            median_val = statistics.median(valid_prices)
            reference_price = min(max(median_val, base_min), max_cap)
        else:
            reference_price = base_min

        target_buy_price = reference_price * (1 - discount_threshold)

        for item in items:
            if item["price_eur"] <= target_buy_price:
                profit = reference_price - item["price_eur"]

                if profit <= 0 or profit > item["price_eur"] * 1.2:
                    continue

                if profit < USER_FILTER["min_profit_eur"]:
                    continue

                if USER_FILTER["max_budget_eur"] and item["price_eur"] > USER_FILTER["max_budget_eur"]:
                    continue

                item["median_eur"] = reference_price
                item["profit_eur"] = profit
                profitable_deals.append(item)

    return profitable_deals


def perform_scan(is_manual=True):
    seen_ads_db = load_seen_ads()
    target_mod = USER_FILTER["target_model"]

    if is_manual:
        info_mod = f" ({target_mod.upper()})" if target_mod else ""
        send_telegram_message(f"🔍 <i>OLX taranıyor{info_mod}... Hesaplamalar yapılıyor.</i>")

    listings = scrape_olx_api(total_items=SCAN_LIMIT, query_filter=target_mod)
    deals = analyze_and_find_deals(listings, discount_threshold=DISCOUNT_THRESHOLD)

    if target_mod:
        deals = [d for d in deals if target_mod.lower() in d["model"].lower()]

    sorted_deals = sorted(deals, key=lambda x: x["profit_eur"], reverse=True)

    if is_manual:
        deals_to_send = sorted_deals[:8]
    else:
        deals_to_send = [d for d in sorted_deals if d["link"] not in seen_ads_db]

    if not deals_to_send:
        if is_manual:
            send_telegram_message("ℹ️ Şu anda piyasa fiyatının altına satılan uygun ilan bulunamadı.")
        return

    for rank, deal in enumerate(deals_to_send, 1):
        seen_ads_db.add(deal["link"])
        header_text = f"🔥 <b>YENİ KÂRLI İLAN: {deal['model']}</b>" if not is_manual else f"🏆 <b>#{rank} FIRSAT: {deal['model']}</b>"
        
        msg = (
            f"{header_text}\n"
            f"📌 <b>İlan:</b> {deal['title']}\n\n"
            f"💵 <b>Alış:</b> {deal['price_eur']:.2f} € (~{deal['price_eur'] * EUR_BGN_RATE:.0f} лв.)\n"
            f"📊 <b>Piyasa Değeri:</b> ~{deal['median_eur']:.2f} € (~{deal['median_eur'] * EUR_BGN_RATE:.0f} лв.)\n"
            f"🚀 <b>Tahmini Kâr: +{deal['profit_eur']:.2f} € (~{deal['profit_eur'] * EUR_BGN_RATE:.0f} лв.)</b>\n\n"
            f"🔗 <a href='{deal['link']}'>İlana Git</a>"
        )
        send_telegram_message(msg)
        time.sleep(1)

    if is_manual:
        summary_lines = [f"📋 <b>KÂR SIRALAMASI ÖZETİ ({len(deals_to_send)} Adet)</b>\n"]
        for rank, deal in enumerate(deals_to_send, 1):
            summary_lines.append(
                f"<b>#{rank}</b> {deal['model']} -> <b>+{deal['profit_eur']:.0f} €</b> (Alış: {deal['price_eur']:.0f} €)"
            )
        send_telegram_message("\n".join(summary_lines))

    save_seen_ads(seen_ads_db)


def telegram_listener():
    global LAST_UPDATE_ID

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

    while True:
        try:
            params = {"offset": LAST_UPDATE_ID + 1, "timeout": 20}
            res = requests.get(url, params=params, timeout=25)
            
            if res.status_code == 200:
                data = res.json().get("result", [])
                for update in data:
                    LAST_UPDATE_ID = update["update_id"]
                    message = update.get("message", {})
                    raw_text = message.get("text", "").strip()
                    lower_text = raw_text.lower()

                    if lower_text in ["/start", "/help", "/yardim"]:
                        help_text = (
                            "👋 <b>OLX Fırsat Takip Botu:</b>\n\n"
                            "🔎 <b>/tara</b> : En kârlı iPhone fırsatlarını listeler.\n"
                            "📱 <b>/model [isim]</b> : Sadece belirtilen modeli takip eder.\n"
                            "💰 <b>/butce [tutar]</b> : Maksimum bütçe sınırı koyar.\n"
                            "📈 <b>/kar [tutar]</b> : Minimum kâr eşiği belirler (Örn: <code>/kar 70</code>)\n"
                            "⚙️ <b>/ayarlar</b> : Aktif ayarları gösterir.\n"
                            "🔄 <b>/oto [ac/kapat]</b> : Otomatik radar taramasını açar/kapatır.\n"
                            "🗑 <b>/sifirla</b> : İlan hafızasını temizler."
                        )
                        send_telegram_message(help_text)

                    elif lower_text == "/tara":
                        perform_scan(is_manual=True)

                    elif lower_text.startswith("/model"):
                        parts = raw_text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].lower() != "hepsi":
                            USER_FILTER["target_model"] = parts[1].strip()
                            send_telegram_message(f"🎯 Model filtresi: <b>{USER_FILTER['target_model']}</b>")
                        else:
                            USER_FILTER["target_model"] = None
                            send_telegram_message("🎯 Tüm iPhone modelleri taranacak.")

                    elif lower_text.startswith("/butce"):
                        parts = raw_text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].isdigit():
                            USER_FILTER["max_budget_eur"] = float(parts[1])
                            send_telegram_message(f"💰 Maksimum bütçe: <b>{USER_FILTER['max_budget_eur']:.0f} €</b>")
                        else:
                            USER_FILTER["max_budget_eur"] = None
                            send_telegram_message("💰 Bütçe sınırı kaldırıldı.")

                    elif lower_text.startswith("/kar"):
                        parts = raw_text.split(maxsplit=1)
                        if len(parts) > 1 and parts[1].isdigit():
                            USER_FILTER["min_profit_eur"] = float(parts[1])
                            send_telegram_message(f"📈 Otomatik bildirim için minimum kâr: <b>+{USER_FILTER['min_profit_eur']:.0f} €</b>")
                        else:
                            USER_FILTER["min_profit_eur"] = MIN_PROFIT_DEFAULT
                            send_telegram_message(f"📈 Kâr şartı varsayılana döndü (+{MIN_PROFIT_DEFAULT:.0f} €).")

                    elif lower_text.startswith("/oto"):
                        if "ac" in lower_text:
                            USER_FILTER["auto_scan"] = True
                            send_telegram_message("▶️ Otomatik fırsat radarı <b>AÇILDI</b> (5 dk).")
                        elif "kapat" in lower_text:
                            USER_FILTER["auto_scan"] = False
                            send_telegram_message("⏸ Otomatik tarama <b>KAPATILDI</b>.")

                    elif lower_text == "/ayarlar":
                        mod = USER_FILTER["target_model"] or "Tümü"
                        btc = f"{USER_FILTER['max_budget_eur']:.0f} €" if USER_FILTER["max_budget_eur"] else "Limitsiz"
                        oto = "Açık (5 dk)" if USER_FILTER["auto_scan"] else "Kapalı"
                        info = (
                            "⚙️️ <b>AKTİF BOT AYARLARI:</b>\n\n"
                            f"📱 <b>Takip Edilen Model:</b> {mod}\n"
                            f"💰 <b>Maks Bütçe:</b> {btc}\n"
                            f"📈 <b>Minimum Kâr Şartı:</b> +{USER_FILTER['min_profit_eur']:.0f} €\n"
                            f"🔄 <b>Otomatik Radar:</b> {oto}"
                        )
                        send_telegram_message(info)

                    elif lower_text == "/sifirla":
                        if os.path.exists(DB_FILE):
                            os.remove(DB_FILE)
                        send_telegram_message("🗑 Hafıza temizlendi.")

        except Exception:
            time.sleep(3)

        time.sleep(1)


def background_auto_scanner():
    while True:
        try:
            if USER_FILTER["auto_scan"]:
                perform_scan(is_manual=False)
        except Exception as e:
            print(f"[!] Radar hatası: {e}")
            
        time.sleep(CHECK_INTERVAL_SEC)


class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot is running 7/24")

    def log_message(self, format, *args):
        return


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
        print(f"[*] Render web portu dinleniyor: {port}")
        httpd.serve_forever()


def main():
    print("[*] iPhone Fırsat Radarı Başlatıldı...")
    
    # 1. Telegram komut dinleyicisi
    listener_thread = threading.Thread(target=telegram_listener, daemon=True)
    listener_thread.start()

    # 2. Otomatik 5 dakikalık arka plan tarayıcısı
    auto_thread = threading.Thread(target=background_auto_scanner, daemon=True)
    auto_thread.start()

    send_telegram_message(
        "🚀 <b>Bot Render Bulutunda 7/24 Aktif!</b>\n\n"
        "• Sistem her 5 dakikada bir sessizce tarar.\n"
        f"• Yalnızca <b>+{MIN_PROFIT_DEFAULT:.0f} € ve üzeri</b> kâr bırakan yeni ilanlar düştüğünde bildirim alacaksın.\n"
        "• Dilediğin zaman <b>/tara</b> yazarak manuel liste çekebilirsin."
    )

    # 3. Render port taramasını (Port scan timeout) çözen web sunucusu
    run_http_server()


if __name__ == "__main__":
    main()
