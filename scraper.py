import os
import re
import json
import datetime
import zoneinfo
import urllib.parse
import requests
import pandas as pd
from bs4 import BeautifulSoup

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
CM_USERNAME = os.environ.get("CM_USERNAME")
CM_PASSWORD = os.environ.get("CM_PASSWORD")

FLARESOLVERR_URL = "http://localhost:8191/v1"
SESSION_ID = "cardmarket_session"

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

def init_flaresolverr_session():
    try:
        requests.post(FLARESOLVERR_URL, json={"cmd": "sessions.destroy", "session": SESSION_ID}, timeout=10)
    except Exception:
        pass

    print(f"Erstelle FlareSolverr-Session '{SESSION_ID}'...")
    res = requests.post(FLARESOLVERR_URL, json={"cmd": "sessions.create", "session": SESSION_ID}, timeout=20)
    print("Session-Status:", res.json().get("message", "OK"))

def login_via_flaresolverr():
    if not CM_USERNAME or not CM_PASSWORD:
        print("⚠️ Keine Login-Credentials hinterlegt.")
        return False

    print(f"Rufe Login-Seite ab für Account '{CM_USERNAME}'...")
    
    get_payload = {
        "cmd": "request.get",
        "url": "https://www.cardmarket.com/de/Pokemon/Login",
        "session": SESSION_ID,
        "maxTimeout": 60000
    }
    res = requests.post(FLARESOLVERR_URL, json=get_payload, timeout=70).json()
    html = res.get("solution", {}).get("response", "")
    soup = BeautifulSoup(html, "html.parser")

    token_input = soup.select_one("input[name='__cmtkn']")
    cmtkn_val = token_input.get("value") if token_input else None

    if not cmtkn_val:
        match = re.search(r'name=["\']__cmtkn["\']\s+value=["\']([^"\']+)["\']', html)
        if match:
            cmtkn_val = match.group(1)

    login_data = {
        "__cmtkn": cmtkn_val if cmtkn_val else "",
        "referalPage": "/de/Pokemon/Login",
        "username": CM_USERNAME,
        "userPassword": CM_PASSWORD
    }

    post_payload = {
        "cmd": "request.post",
        "url": "https://www.cardmarket.com/de/Pokemon/PostGetAction/User_Login",
        "session": SESSION_ID,
        "postData": urllib.parse.urlencode(login_data),
        "headers": {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.cardmarket.com/de/Pokemon/Login",
            "Origin": "https://www.cardmarket.com"
        },
        "maxTimeout": 60000
    }
    
    print("Sende Login-POST an Cardmarket...")
    requests.post(FLARESOLVERR_URL, json=post_payload, timeout=70)
    
    verify_payload = {
        "cmd": "request.get",
        "url": "https://www.cardmarket.com/de/Pokemon",
        "session": SESSION_ID,
        "maxTimeout": 60000
    }
    verify_res = requests.post(FLARESOLVERR_URL, json=verify_payload, timeout=70).json()
    verify_html = verify_res.get("solution", {}).get("response", "")

    if "EINKAUFSWAGEN" in verify_html or (CM_USERNAME and CM_USERNAME.lower() in verify_html.lower()):
        print("✅ LOGIN-STATUS: ERFOLGREICH EINGELOGGT!")
        return True
    else:
        print("⚠️ Login verarbeitet, prüfe Status auf Produktseite.")
        return False

def fetch_html_via_flaresolverr(target_url):
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "session": SESSION_ID,
        "maxTimeout": 60000
    }
    try:
        resp = requests.post(FLARESOLVERR_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=70)
        data = resp.json()
        if data.get("status") == "ok":
            return data.get("solution", {}).get("response")
        return None
    except Exception as e:
        print(f"Verbindungsfehler zu FlareSolverr: {e}")
        return None

def calc_mean(prices):
    if not prices:
        return None
    return round(sum(prices) / len(prices), 2)

def run_scraper():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    init_flaresolverr_session()
    login_via_flaresolverr()

    berlin_tz = zoneinfo.ZoneInfo("Europe/Berlin")
    timestamp = datetime.datetime.now(berlin_tz).strftime("%Y-%m-%d %H:%M:%S")
    results = []

    for item in config["products"]:
        p_name = item["name"]
        p_type = item.get("type", "single").lower()
        p_url = item["url"]

        print(f"\n==========================================")
        print(f"Scrape: {p_name} (Typ: {p_type})")

        html = fetch_html_via_flaresolverr(p_url)
        if not html:
            print(f"Konnte {p_name} nicht abrufen.")
            continue

        soup = BeautifulSoup(html, "html.parser")

        # 1. Gesamtmenge erfassen
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

        # 2. Angebote auslesen
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

        sorted_item_prices = sorted(parsed_item_prices)
        sorted_total_prices = sorted(parsed_total_prices)

        if sorted_total_prices:
            c1_total = sorted_total_prices[0] if len(sorted_total_prices) > 0 else None
            c2_total = sorted_total_prices[1] if len(sorted_total_prices) > 1 else None
            c3_total = sorted_total_prices[2] if len(sorted_total_prices) > 2 else None

            if p_type == "case":
                avg_robust = calc_mean(sorted_item_prices[1:5])
                avg_market = calc_mean(sorted_item_prices[:10])
                avg_robust_shipping = calc_mean(sorted_total_prices[1:5])
                avg_market_shipping = calc_mean(sorted_total_prices[:10])
            else:
                avg_robust = calc_mean(sorted_item_prices[2:10])
                avg_market = calc_mean(sorted_item_prices[:15])
                avg_robust_shipping = calc_mean(sorted_total_prices[2:10])
                avg_market_shipping = calc_mean(sorted_total_prices[:15])

            print(f"Top 3 Gesamt: [{c1_total}€, {c2_total}€, {c3_total}€] | Robust={avg_robust_shipping}€ | Markt={avg_market_shipping}€")

            results.append({
                "timestamp": timestamp,
                "product_name": p_name,
                "product_type": p_type,
                "available_items": avail_items,
                "cheapest_1": c1_total,
                "cheapest_2": c2_total,
                "cheapest_3": c3_total,
                "avg_robust": avg_robust,
                "avg_market": avg_market,
                "avg_robust_shipping": avg_robust_shipping,
                "avg_market_shipping": avg_market_shipping
            })

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

if __name__ == "__main__":
    run_scraper()
