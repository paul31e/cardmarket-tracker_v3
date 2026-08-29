import os
import re
import json
import datetime
import requests
import pandas as pd
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
TELEGRAM_BOT_TOKEN = os.environ.get("BOTFATHER")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAMCHATID")

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
        requests.post(url_api, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

def parse_price(price_str):
    if not price_str:
        return None
    # Filtert 1.234,56 € oder 123,45 €
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
                "--disable-setuid-sandbox",
                "--disable-web-security"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="de-DE",
            timezone_id="Europe/Berlin"
        )
        page = context.new_page()
        stealth_sync(page)

        for item in config["products"]:
            print(f"\n==========================================")
            print(f"Scrape: {item['name']}")
            print(f"URL: {item['url']}")
            try:
                response = page.goto(item["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)

                page_title = page.title()
                print(f"Seitentitel: {page_title}")

                # Cookie-Banner wegklicken
                for sel in ["button:has-text('Alle akzeptieren')", "button:has-text('Accept all')", "#btn-accept-all", ".btn-primary"]:
                    try:
                        btn = page.locator(sel)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.click()
                            page.wait_for_timeout(1000)
                            break
                    except Exception:
                        pass

                # 1. Verfügbare Artikel ermitteln
                avail_items = 0
                dt_loc = page.locator("dt:has-text('Verfügbare Artikel'), dt:has-text('Available items')")
                if dt_loc.count() > 0:
                    dd_text = dt_loc.first.locator("xpath=following-sibling::dd[1]").inner_text()
                    avail_items = int(re.sub(r"[^\d]", "", dd_text))
                else:
                    body_text = page.locator("body").inner_text()
                    match = re.search(r"(?:Verfügbare Artikel|Available items)[\s:]*([0-9.]+)", body_text, re.IGNORECASE)
                    if match:
                        avail_items = int(match.group(1).replace(".", ""))

                print(f"Verfügbare Menge ermittelt: {avail_items}")

                # 2. Angebotszeilen parsen
                rows = page.locator("div.article-row, .table-body > div.row, div[id^='articleRow']").all()
                parsed_offers = []

                for row in rows:
                    txt = row.inner_text()
                    # Alle Preise in der Zeile finden
                    matches = re.findall(r"(\d+(?:,\d{2})?)\s*€", txt)
                    if matches:
                        p_item = parse_price(matches[0])
                        if p_item and p_item > 0:
                            p_ship = parse_price(matches[1]) if len(matches) > 1 else 0.0
                            parsed_offers.append({
                                "item_price": p_item,
                                "total_price": p_item + p_ship
                            })

                print(f"Gefundene Angebote: {len(parsed_offers)}")

                # Falls leer: Debug-Screenshot speichern
                if not parsed_offers:
                    print(f"⚠️ Keine Angebote gefunden! Erstelle Debug-Screenshot...")
                    os.makedirs("debug", exist_ok=True)
                    page.screenshot(path="debug/failed_page.png", full_page=True)
                    with open("debug/failed_page.html", "w", encoding="utf-8") as dump:
                        dump.write(page.content())

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

            except Exception as e:
                print(f"Fehler bei {item['name']}: {e}")

        browser.close()

    # Immer data-Ordner und CSV aktualisieren
    os.makedirs("data", exist_ok=True)
    if results:
        df_new = pd.DataFrame(results)
        if os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > 0:
            df_existing = pd.read_csv(CSV_PATH)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(CSV_PATH, index=False)
        else:
            df_new.to_csv(CSV_PATH, index=False)
        print("\n=> data/data.csv wurde erfolgreich mit neuen Daten befüllt!")
    else:
        print("\n=> Keine Daten erfasst (siehe Debug-Dateien falls angelegt).")

if __name__ == "__main__":
    run_scraper()
