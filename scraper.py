import os
import re
import json
import datetime
import requests
import pandas as pd
from playwright.sync_api import sync_playwright

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
TELEGRAM_BOT_TOKEN = os.environ.get("BOTFATHER")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAMCHATID")

def send_telegram_alert(product_name, price, target, url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram-Credentials fehlen oder nicht gesetzt.")
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
        print(f"Telegram Status: {res.status_code}")
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

def run_scraper():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="de-DE",
            timezone_id="Europe/Berlin"
        )
        page = context.new_page()

        for item in config["products"]:
            print(f"\n--- Scrape: {item['name']} ---")
            print(f"URL: {item['url']}")
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)

                # Cookie-Banner schließen falls vorhanden
                try:
                    cookie_btn = page.locator("button:has-text('Alle akzeptieren'), button:has-text('Accept all'), #btn-accept-all")
                    if cookie_btn.count() > 0 and cookie_btn.first.is_visible():
                        cookie_btn.first.click()
                        page.wait_for_timeout(1000)
                except Exception:
                    pass

                # 1. Verfügbare Artikel auslesen
                avail_items = 0
                body_text = page.locator("body").inner_text()
                match = re.search(r"(?:Verfügbare Artikel|Available items|Total items)[\s:]*([0-9.]+)", body_text, re.IGNORECASE)
                if match:
                    avail_items = int(match.group(1).replace(".", ""))
                    print(f"Verfügbare Menge: {avail_items}")
                else:
                    print("Konnte verfügbare Artikelmenge nicht ermitteln.")

                # 2. Angebote auslesen
                parsed_offers = []
                
                # Versuche spezifische Artikel-Zeilen
                rows = page.locator(".article-row, .table-body > div").all()
                for row in rows:
                    txt = row.inner_text()
                    prices = re.findall(r"(\d+(?:,\d{2})?)\s*€", txt)
                    if prices:
                        item_p = parse_price(prices[0])
                        if item_p and item_p > 0:
                            ship_p = parse_price(prices[1]) if len(prices) > 1 else 0.0
                            parsed_offers.append({
                                "item_price": item_p,
                                "total_price": item_p + ship_p
                            })

                # Fallback: Falls keine Zeilen gefunden wurden, alle Euro-Preise der Seite parsen
                if not parsed_offers:
                    all_prices = re.findall(r"(\d+(?:,\d{2})?)\s*€", body_text)
                    valid_prices = [parse_price(p) for p in all_prices if parse_price(p) and parse_price(p) > 1.0]
                    for p_val in valid_prices[:15]:
                        parsed_offers.append({"item_price": p_val, "total_price": p_val})

                print(f"Gefundene Angebote: {len(parsed_offers)}")

                if parsed_offers:
                    # 3. Durchschnitt berechnen
                    limit = 10 if item.get("type") == "single" else 3
                    avg_slice = [o["item_price"] for o in parsed_offers[:limit]]
                    avg_price = round(sum(avg_slice) / len(avg_slice), 2)

                    # 4. Top 3 Endpreise
                    sorted_by_total = sorted(parsed_offers, key=lambda x: x["total_price"])
                    c1 = sorted_by_total[0]["total_price"] if len(sorted_by_total) > 0 else None
                    c2 = sorted_by_total[1]["total_price"] if len(sorted_by_total) > 1 else None
                    c3 = sorted_by_total[2]["total_price"] if len(sorted_by_total) > 2 else None

                    print(f"Preise: Top1={c1}€, Schnitt={avg_price}€")

                    # 5. Alert Trigger
                    target = item.get("target_price", 0)
                    if c1 and c1 <= target:
                        print(f"Alert getriggert: {c1}€ <= {target}€")
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

            except Exception as e:
                print(f"Fehler bei {item['name']}: {e}")

        browser.close()

    # Daten in data/data.csv schreiben
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
        print("\nKeine Datensätze extrahiert.")

if __name__ == "__main__":
    run_scraper()
