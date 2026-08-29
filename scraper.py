import os
import re
import json
import datetime
import requests
import pandas as pd
from bs4 import BeautifulSoup

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
TELEGRAM_BOT_TOKEN = os.environ.get("BOTFATHER")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAMCHATID")

FLARESOLVERR_URL = "http://localhost:8191/v1"

def send_telegram_alert(product_name, price, target, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram-Credentials fehlen.")
        return
    text = (
        f"🚨 *PREIS-ALERT: {product_name}* 🚨\n\n"
        f"💰 Günstigster Preis: *{price:.2f} €*\n"
        f"🎯 Zielpreis: *{target:.2f} €*\n\n"
        f"🔗 [Direkt zum Angebot]({url})"
    )
    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url_api, json=payload, timeout=10)
        print(f"Telegram Alert Status: {res.status_code}")
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

def parse_price(price_str):
    if not price_str:
        return None
    clean = price_str.replace("€", "").replace("\xa0", "").strip()
    clean = clean.replace(".", "")
    clean = clean.replace(",", ".")
    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None

def fetch_html_via_flaresolverr(target_url):
    """Sendet Anfrage an den lokalen FlareSolverr-Container"""
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": 60000
    }
    headers = {"Content-Type": "application/json"}

    try:
        print("Sende Request an lokalen FlareSolverr-Service...")
        resp = requests.post(FLARESOLVERR_URL, json=payload, headers=headers, timeout=70)
        data = resp.json()
        
        if data.get("status") == "ok":
            solution = data.get("solution", {})
            status_code = solution.get("status")
            print(f"FlareSolverr Response Status: {status_code}")
            return solution.get("response")
        else:
            print(f"FlareSolverr Fehler: {data.get('message')}")
            return None
    except Exception as e:
        print(f"Verbindungsfehler zu FlareSolverr: {e}")
        return None

def run_scraper():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    for item in config["products"]:
        print(f"\n==========================================")
        print(f"Scrape: {item['name']}")
        print(f"URL: {item['url']}")

        html = fetch_html_via_flaresolverr(item["url"])
        if not html:
            print(f"Konnte {item['name']} nicht abrufen.")
            continue

        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("title")
        print(f"Seitentitel: {title.get_text(strip=True) if title else 'Kein Titel'}")

        # 1. Verfügbare Artikel auslesen
        avail_items = 0
        for dt in soup.find_all("dt"):
            txt = dt.get_text(strip=True)
            if "Verfügbare Artikel" in txt or "Available items" in txt:
                dd = dt.find_next_sibling("dd")
                if dd:
                    digits = re.sub(r"[^\d]", "", dd.get_text())
                    if digits:
                        avail_items = int(digits)
                    break

        print(f"Verfügbare Menge: {avail_items}")

        # 2. Angebote auslesen
        offer_rows = soup.select("div[id^='articleRow'], .article-row")
        parsed_prices = []

        for row in offer_rows:
            price_elem = row.select_one(".col-price, .price-container, .font-weight-bold")
            if price_elem:
                p_text = price_elem.get_text(strip=True)
                match = re.search(r"([\d.]+,\d{2})\s*€?", p_text)
                if match:
                    val = parse_price(match.group(1))
                    if val and val > 1.0:
                        parsed_prices.append(val)

        if not parsed_prices:
            for row in offer_rows:
                matches = re.findall(r"(\d+(?:\.\d{3})*,\d{2})\s*€", row.get_text())
                for m in matches:
                    val = parse_price(m)
                    if val and val > 1.0:
                        parsed_prices.append(val)
                        break

        print(f"Extrahierte Angebotspreise ({len(parsed_prices)}): {parsed_prices[:5]}...")

        if parsed_prices:
            limit = 10 if item.get("type") == "single" else 3
            avg_slice = parsed_prices[:limit]
            avg_price = round(sum(avg_slice) / len(avg_slice), 2)

            sorted_prices = sorted(parsed_prices)
            c1 = sorted_prices[0] if len(sorted_prices) > 0 else None
            c2 = sorted_prices[1] if len(sorted_prices) > 1 else None
            c3 = sorted_prices[2] if len(sorted_prices) > 2 else None

            print(f"-> Top 1: {c1}€ | Top 2: {c2}€ | Top 3: {c3}€ | Schnitt ({limit}): {avg_price}€")

            target = item.get("target_price", 0)
            if c1 and c1 <= target:
                print(f"-> Alert getriggert: {c1}€ <= {target}€")
                send_telegram_alert(item["name"], c1, target, item["url"])

            results.append({
                "timestamp": timestamp,
                "product_name": item["name"],
                "avg_item_price": avg_price,
                "available_items": avail_items,
                "cheapest_total_1": c1,
                "cheapest_total_2": c2,
                "cheapest_total_3": c3
            })

    # In CSV schreiben
    os.makedirs("data", exist_ok=True)
    if results:
        df_new = pd.DataFrame(results)
        if os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > 0:
            df_existing = pd.read_csv(CSV_PATH)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(CSV_PATH, index=False)
        else:
            df_new.to_csv(CSV_PATH, index=False)
        print("\n=> Daten erfolgreich in data/data.csv gespeichert!")
    else:
        print("\n=> Keine Daten erfasst.")

if __name__ == "__main__":
    run_scraper()
