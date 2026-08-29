import os
import re
import json
import time
import datetime
import requests
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
TELEGRAM_BOT_TOKEN = os.environ.get("BOTFATHER")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAMCHATID")
CM_USERNAME = os.environ.get("CM_USERNAME")
CM_PASSWORD = os.environ.get("CM_PASSWORD")

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
    clean = price_str.replace("€", "").replace("+", "").replace("\xa0", "").strip()
    clean = clean.replace(".", "")
    clean = clean.replace(",", ".")
    try:
        val = float(clean)
        return val if val >= 0 else None
    except ValueError:
        return None

def calc_mean(prices):
    if not prices:
        return None
    return round(sum(prices) / len(prices), 2)

def perform_browser_login(page):
    """Loggt sich automatisiert über das Cardmarket-Webformular ein."""
    if not CM_USERNAME or not CM_PASSWORD:
        print("⚠️ Keine Login-Daten hinterlegt. Scrape läuft als Gast.")
        return False

    print(f"Starte Login für Account '{CM_USERNAME}'...")
    page.goto("https://www.cardmarket.com/de/Pokemon", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    if page.locator("a[href*='/User/'], .user-nav, .account-button").count() > 0:
        print("✅ Bereits eingeloggt!")
        return True

    try:
        login_link = page.locator("a[href*='/Login'], button:has-text('Anmelden'), a:has-text('Anmelden')").first
        if login_link.count() > 0 and login_link.is_visible():
            login_link.click()
            time.sleep(2)

        user_input = page.locator("input[name='username'], input[name='_username'], input[id='username']").first
        pass_input = page.locator("input[name='userPassword'], input[name='password'], input[name='_password'], input[type='password']").first

        if user_input.count() > 0 and pass_input.count() > 0:
            user_input.fill(CM_USERNAME)
            pass_input.fill(CM_PASSWORD)
            
            submit_btn = page.locator("input[type='submit'], button[type='submit'], input[value='Anmelden']").first
            if submit_btn.count() > 0:
                submit_btn.click()
            else:
                pass_input.press("Enter")

            page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(4)

            html_after = page.content()
            if "EINKAUFSWAGEN" in html_after or CM_USERNAME.lower() in html_after.lower():
                print("✅ Login erfolgreich ausgeführt!")
                return True
            else:
                print("⚠️ Login abgeschickt, aber User-Badge nicht eindeutig gefunden.")
                return False
        else:
            print("❌ Login-Felder nicht gefunden.")
            return False

    except Exception as e:
        print(f"Fehler während des Logins: {e}")
        return False

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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="de-DE"
        )
        page = context.new_page()
        stealth_sync(page)

        # Login durchführen
        perform_browser_login(page)

        for item in config["products"]:
            p_name = item["name"]
            p_type = item.get("type", "single").lower()
            p_url = item["url"]
            target = item.get("target_price", 0)

            print(f"\n==========================================")
            print(f"Scrape: {p_name} (Typ: {p_type})")
            print(f"URL: {p_url}")

            try:
                page.goto(p_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(4)
                html = page.content()
            except Exception as e:
                print(f"Fehler beim Aufrufen der URL: {e}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            title = soup.find("title")
            print(f"Seitentitel: {title.get_text(strip=True) if title else 'Kein Titel'}")

            # Status-Check
            if "PAUL2403E" in html or "EINKAUFSWAGEN" in html:
                print("✅ LOGIN-STATUS: EINGELOGGT")
            else:
                print("❌ LOGIN-STATUS: NICHT EINGELOGGT")

            # 1. Verfügbare Menge
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

            # 2. Angebote & Preise extrahieren
            offer_rows = soup.select("div[id^='articleRow'], .article-row")
            parsed_item_prices = []
            parsed_total_prices = []

            for row in offer_rows:
                price_cell = row.select_one(".col-price, .price-container, .font-weight-bold")
                text_to_search = price_cell.get_text(" ", strip=True) if price_cell else row.get_text(" ", strip=True)
                
                euro_matches = re.findall(r"(\d+(?:\.\d{3})*,\d{2})\s*€", text_to_search)
                
                if euro_matches:
                    item_price = parse_price(euro_matches[0])
                    if item_price and item_price > 1.0:
                        shipping_cost = 0.0
                        if len(euro_matches) > 1:
                            parsed_ship = parse_price(euro_matches[1])
                            if parsed_ship is not None:
                                shipping_cost = parsed_ship
                        
                        total_price = round(item_price + shipping_cost, 2)
                        parsed_item_prices.append(item_price)
                        parsed_total_prices.append(total_price)

            print(f"Extrahierte Artikelpreise ({len(parsed_item_prices)}): {parsed_item_prices[:3]}...")
            print(f"Extrahierte Gesamtpreise  ({len(parsed_total_prices)}): {parsed_total_prices[:3]}...")

            if parsed_item_prices:
                c1 = parsed_item_prices[0] if len(parsed_item_prices) > 0 else None
                c2 = parsed_item_prices[1] if len(parsed_item_prices) > 1 else None
                c3 = parsed_item_prices[2] if len(parsed_item_prices) > 2 else None

                # 3. Durchschnitte berechnen
                if p_type == "case":
                    avg_robust = calc_mean(parsed_item_prices[1:5])
                    avg_market = calc_mean(parsed_item_prices[:10])
                    avg_robust_shipping = calc_mean(parsed_total_prices[1:5])
                    avg_market_shipping = calc_mean(parsed_total_prices[:10])
                else:
                    avg_robust = calc_mean(parsed_item_prices[2:10])
                    avg_market = calc_mean(parsed_item_prices[:15])
                    avg_robust_shipping = calc_mean(parsed_total_prices[2:10])
                    avg_market_shipping = calc_mean(parsed_total_prices[:15])

                print(f"Artikelpreise: Robust={avg_robust}€ | Markt={avg_market}€")
                print(f"Inkl. Versand: Robust={avg_robust_shipping}€ | Markt={avg_market_shipping}€")

                # 4. Alert prüfen
                if c1 and target and c1 <= target:
                    print(f"-> Alert getriggert: {c1}€ <= {target}€")
                    send_telegram_alert(p_name, c1, target, p_url)

                results.append({
                    "timestamp": timestamp,
                    "product_name": p_name,
                    "product_type": p_type,
                    "available_items": avail_items,
                    "cheapest_1": c1,
                    "cheapest_2": c2,
                    "cheapest_3": c3,
                    "avg_robust": avg_robust,
                    "avg_market": avg_market,
                    "avg_robust_shipping": avg_robust_shipping,
                    "avg_market_shipping": avg_market_shipping
                })

        browser.close()

    # CSV schreiben
    os.makedirs("data", exist_ok=True)
    if results:
        df_new = pd.DataFrame(results)
        if os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > 0:
            df_existing = pd.read_csv(CSV_PATH)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(CSV_PATH, index=False)
        else:
            df_new.to_csv(CSV_PATH, index=False)
        print("\n=> data/data.csv wurde erfolgreich aktualisiert!")
    else:
        print("\n=> Keine Daten erfasst.")

if __name__ == "__main__":
    run_scraper()
