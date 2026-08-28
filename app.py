import os
import re
import json
import datetime
import requests
import pandas as pd
from playwright.sync_api import sync_playwright

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_alert(product_name, price, target, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram-Credentials fehlen, überspringe Alert.")
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
    requests.post(url_api, json=payload)

def parse_price(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d,]", "", price_str).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None

def run_scraper():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ).new_page()

        for item in config["products"]:
            print(f"Scrape: {item['name']}...")
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)

                # 1. Verfügbare Artikel aus Infobox auslesen
                avail_items = 0
                stat_elems = page.locator("dt:has-text('Verfügbare Artikel'), dt:has-text('Available items') + dd").all_inner_texts()
                if not stat_elems:
                    # Alternativer Selektor
                    stat_block = page.locator(".info-list-container").inner_text()
                    match = re.search(r"(?:Verfügbare Artikel|Available items)\s*([\d.]+)", stat_block)
                    if match:
                        avail_items = int(match.group(1).replace(".", ""))
                else:
                    avail_items = int(re.sub(r"[^\d]", "", stat_elems[0]))

                # 2. Tabellenzeilen für Angebote
                rows = page.locator(".article-row").all()
                parsed_offers = []

                for row in rows:
                    item_price_raw = row.locator(".color-primary").first.inner_text()
                    item_price = parse_price(item_price_raw)
                    if not item_price:
                        continue

                    # Versandkosten extrahieren (falls vorhanden, sonst Fallback auf Basiswert)
                    shipping_loc = row.locator(".shipping-cost, .text-muted small")
                    shipping_price = 0.0
                    if shipping_loc.count() > 0:
                        parsed_ship = parse_price(shipping_loc.first.inner_text())
                        shipping_price = parsed_ship if parsed_ship else 0.0

                    total_price = item_price + shipping_price
                    parsed_offers.append({
                        "item_price": item_price,
                        "total_price": total_price
                    })

                if not parsed_offers:
                    print(f"Keine Angebote für {item['name']} gefunden.")
                    continue

                # 3. Durchschnittsberechnung (Top 10 bei Single, Top 3 bei Case)
                limit = 10 if item["type"] == "single" else 3
                avg_slice = [o["item_price"] for o in parsed_offers[:limit]]
                avg_price = round(sum(avg_slice) / len(avg_slice), 2) if avg_slice else None

                # 4. Top 3 Endpreise (sortiert nach total_price)
                sorted_by_total = sorted(parsed_offers, key=lambda x: x["total_price"])
                c1 = sorted_by_total[0]["total_price"] if len(sorted_by_total) > 0 else None
                c2 = sorted_by_total[1]["total_price"] if len(sorted_by_total) > 1 else None
                c3 = sorted_by_total[2]["total_price"] if len(sorted_by_total) > 2 else None

                # 5. Alert Trigger
                if c1 and c1 <= item["target_price"]:
                    send_telegram_alert(item["name"], c1, item["target_price"], item["url"])

                results.append({
                    "timestamp": timestamp,
                    "product_name": item["name"],
                    "avg_item_price": avg_price,
                    "available_items": avail_items,
                    "cheapest_total_1": c1,
                    "cheapest_total_2": c2,
                    "cheapest_total_3": c3
                })

            except Exception as e:
                print(f"Fehler bei {item['name']}: {e}")

        browser.close()

    # In CSV schreiben / anhängen
    if results:
        df_new = pd.DataFrame(results)
        if os.path.exists(CSV_PATH):
            df_existing = pd.read_csv(CSV_PATH)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(CSV_PATH, index=False)
        else:
            os.makedirs("data", exist_ok=True)
            df_new.to_csv(CSV_PATH, index=False)

if __name__ == "__main__":
    run_scraper()
