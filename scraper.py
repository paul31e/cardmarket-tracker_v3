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
SCRAPEOPS_API_KEY = os.environ.get("SCRAPEOPS_API_KEY")

def send_telegram_alert(product_name, price, target, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram-Credentials fehlen.")
        return
    text = (
        f"🚨 *PREIS-ALERT: {product_name}* 🚨\n\n"
        f"💰 Günstigster Endpreis: *{price:.2f} €* (inkl. Versand)\n"
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
    cleaned = re.sub(r"[^\d,]", "", price_str).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None

def fetch_html_via_proxy(target_url):
    """Ruft Cardmarket über ScrapeOps Residential Proxy auf, um Cloudflare zu umgehen."""
    if not SCRAPEOPS_API_KEY:
        print("⚠️ FEHLER: SCRAPEOPS_API_KEY Secret fehlt in GitHub!")
        return None
    
    proxy_url = "https://proxy.scrapeops.io/v1/"
    params = {
        "api_key": SCRAPEOPS_API_KEY,
        "url": target_url,
        "residential": "true",
        "country": "de"
    }
    try:
        resp = requests.get(proxy_url, params=params, timeout=60)
        if resp.status_code == 200:
            return resp.text
        else:
            print(f"Proxy Fehler-Status: {resp.status_code}")
            return None
    except Exception as e:
        print(f"Fehler beim Proxy-Abruf: {e}")
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

        html = fetch_html_via_proxy(item["url"])
        if not html:
            print("Konnte Seite nicht abrufen.")
            continue

        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("title")
        print(f"Seitentitel: {title.get_text(strip=True) if title else 'Kein Titel'}")

        # 1. Verfügbare Artikel auslesen
        avail_items = 0
        for dt in soup.find_all("dt"):
            if "Verfügbare Artikel" in dt.get_text() or "Available items" in dt.get_text():
                dd = dt.find_next_sibling("dd")
                if dd:
                    digits = re.sub(r"[^\d]", "", dd.get_text())
                    if digits:
                        avail_items = int(digits)
                    break

        if avail_items == 0:
            match = re.search(r"(?:Verfügbare Artikel|Available items)[\s:]*([0-9.]+)", html)
            if match:
                avail_items = int(match.group(1).replace(".", ""))

        print(f"Verfügbare Menge: {avail_items}")

        # 2. Angebote auslesen
        parsed_offers = []
        rows = soup.select(".article-row, div[id^='articleRow']")
        for row in rows:
            txt = row.get_text(separator=" ")
            prices = re.findall(r"(\d+(?:,\d{2})?)\s*€", txt)
            if prices:
                item_p = parse_price(prices[0])
                if item_p and item_p > 0:
                    ship_p = parse_price(prices[1]) if len(prices) > 1 else 0.0
                    parsed_offers.append({
                        "item_price": item_p,
                        "total_price": item_p + ship_p
                    })

        print(f"Gefundene Angebote: {len(parsed_offers)}")

        if parsed_offers:
            limit = 10 if item.get("type") == "single" else 3
            avg_slice = [o["item_price"] for o in parsed_offers[:limit]]
            avg_price = round(sum(avg_slice) / len(avg_slice), 2)

            sorted_by_total = sorted(parsed_offers, key=lambda x: x["total_price"])
            c1 = sorted_by_total[0]["total_price"] if len(sorted_by_total) > 0 else None
            c2 = sorted_by_total[1]["total_price"] if len(sorted_by_total) > 1 else None
            c3 = sorted_by_total[2]["total_price"] if len(sorted_by_total) > 2 else None

            print(f"-> Top 1: {c1}€ | Schnitt: {avg_price}€")

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
