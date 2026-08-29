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

def calc_mean(prices):
    """Hilfsfunktion zur sicheren Berechnung des Mittelwerts."""
    if not prices:
        return None
    return round(sum(prices) / len(prices), 2)

def run_scraper():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    for item in config["products"]:
        p_name = item["name"]
        p_type = item.get("type", "single").lower()
        p_url = item["url"]
        target = item.get("target_price", 0)

        print(f"\n==========================================")
        print(f"Scrape: {p_name} (Typ: {p_type})")
        print(f"URL: {p_url}")

        html = fetch_html_via_flaresolverr(p_url)
        if not html:
            print(f"Konnte {p_name} nicht abrufen.")
            continue

        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("title")
        print(f"Seitentitel: {title.get_text(strip=True) if title else 'Kein Titel'}")

        # 1. Gesamt-Menge erfassen
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

        # 2. Angebote aus Tabelle extrahieren
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

        sorted_prices = sorted(parsed_prices)
        print(f"Extrahierte Preise ({len(sorted_prices)}): {sorted_prices[:5]}...")

        if sorted_prices:
            c1 = sorted_prices[0] if len(sorted_prices) > 0 else None
            c2 = sorted_prices[1] if len(sorted_prices) > 1 else None
            c3 = sorted_prices[2] if len(sorted_prices) > 2 else None

            # 3. Spezifische Durchschnittsberechnung nach Typ
            if p_type == "case":
                # Robust: Rang 2 bis 5 (Index 1 bis 5)
                # Gesamt: Rang 1 bis 10 (Index 0 bis 10)
                avg_robust = calc_mean(sorted_prices[1:5])
                avg_market = calc_mean(sorted_prices[:10])
                print(f"Case-Logik  -> Top 1: {c1}€ | Robust (Rang 2-5): {avg_robust}€ | Markt (Rang 1-10): {avg_market}€")
            else:
                # Robust: Rang 3 bis 10 (Index 2 bis 10)
                # Gesamt: Rang 1 bis 15 (Index 0 bis 15)
                avg_robust = calc_mean(sorted_prices[2:10])
                avg_market = calc_mean(sorted_prices[:15])
                print(f"Single-Logik -> Top 1: {c1}€ | Robust (Rang 3-10): {avg_robust}€ | Markt (Rang 1-15): {avg_market}€")

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
                "avg_market": avg_market
            })

    # In CSV schreiben
    os.makedirs("data", exist_ok=True)
    if results:
        df_new = pd.DataFrame(results)
        if os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > 0:
            df_existing = pd.read_csv(CSV_PATH)
            # Spaltenstruktur vereinheitlichen
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(CSV_PATH, index=False)
        else:
            df_new.to_csv(CSV_PATH, index=False)
        print("\n=> data/data.csv wurde erfolgreich aktualisiert!")
    else:
        print("\n=> Keine Daten erfasst.")

if __name__ == "__main__":
    run_scraper()
