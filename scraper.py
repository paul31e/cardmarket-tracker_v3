import os
import sys
import re
import json
import datetime
import urllib.parse
import smtplib
from email.mime.text import MIMEText
import requests
import pandas as pd
from bs4 import BeautifulSoup

CSV_PATH = "data/data.csv"
CONFIG_PATH = "config.json"
CM_USERNAME = os.environ.get("CM_USERNAME")
CM_PASSWORD = os.environ.get("CM_PASSWORD")

FLARESOLVERR_URL = "http://localhost:8191/v1"
SESSION_ID = "cardmarket_auth_session"

# E-Mail Konfiguration (SMTP)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO")

# Telegram & Supabase Konfiguration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = "https://nxtpixbuesueouszfocg.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im54dHBpeGJ1ZXN1ZW91c3pmb2NnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODgxMDY1NjUsImV4cCI6MjEwMzY4MjU2NX0.MgQqiuqGCACeeR7K7bRw9p6sbjc1lHip60zXXT_bpGc"


def parse_price(price_str):
    if not price_str:
        return None
    clean = price_str.replace("€", "").replace("+", "").replace("\xa0", "").strip()
    clean = clean.replace(".", "").replace(",", ".")
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

    print(f"🔧 Erstelle frische FlareSolverr-Session '{SESSION_ID}'...")
    res = requests.post(FLARESOLVERR_URL, json={"cmd": "sessions.create", "session": SESSION_ID}, timeout=20)
    print("   Session-Status:", res.json().get("message", "OK"))


def login_and_verify():
    if not CM_USERNAME or not CM_PASSWORD:
        print("❌ FEHLER: CM_USERNAME oder CM_PASSWORD Umgebungsvariablen fehlen!")
        sys.exit(1)

    print(f"🔑 Starte Login-Prozess für Benutzer '{CM_USERNAME}'...")

    # 1. Login-Seite abrufen (Cloudflare lösen & CSRF Token holen)
    get_payload = {
        "cmd": "request.get",
        "url": "https://www.cardmarket.com/de/Pokemon/Login",
        "session": SESSION_ID,
        "maxTimeout": 60000
    }
    try:
        res = requests.post(FLARESOLVERR_URL, json=get_payload, timeout=70).json()
        html = res.get("solution", {}).get("response", "")
    except Exception as e:
        print(f"❌ Fehler beim Laden der Login-Seite: {e}")
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.select_one("input[name='__cmtkn']")
    cmtkn_val = token_input.get("value") if token_input else None

    if not cmtkn_val:
        match = re.search(r'name=["\']__cmtkn["\']\s+value=["\']([^"\']+)["\']', html)
        if match:
            cmtkn_val = match.group(1)

    if not cmtkn_val:
        print("❌ CSRF-Token (__cmtkn) konnte nicht von der Login-Seite extrahiert werden.")
        sys.exit(1)

    # 2. Login POST absenden
    login_data = {
        "__cmtkn": cmtkn_val,
        "referalPage": "/de/Pokemon",
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

    print("   Sende Login-POST an Cardmarket...")
    try:
        requests.post(FLARESOLVERR_URL, json=post_payload, timeout=70)
    except Exception as e:
        print(f"❌ Login POST Request fehlgeschlagen: {e}")
        sys.exit(1)

    # 3. Verifikation: Login-Status im HTML überprüfen
    print("🔍 Überprüfe Login-Status auf Cardmarket...")
    verify_payload = {
        "cmd": "request.get",
        "url": "https://www.cardmarket.com/de/Pokemon",
        "session": SESSION_ID,
        "maxTimeout": 60000
    }
    
    try:
        verify_res = requests.post(FLARESOLVERR_URL, json=verify_payload, timeout=70).json()
        verify_html = verify_res.get("solution", {}).get("response", "")
    except Exception as e:
        print(f"❌ Verifikationsabruf fehlgeschlagen: {e}")
        sys.exit(1)

    is_authenticated = (
        (CM_USERNAME and CM_USERNAME.lower() in verify_html.lower()) or
        "Logout" in verify_html or
        "Abmelden" in verify_html or
        "user-nav" in verify_html
    )

    if is_authenticated:
        print(f"✅ LOGIN ERFOLGREICH BESTÄTIGT! Angemeldet als '{CM_USERNAME}'.")
    else:
        print("❌ LOGIN FEHLGESCHLAGEN! Weder Benutzername noch Abmelde-Status im HTML gefunden.")
        print("⚠️ Breche Scraping-Prozess ab, um fehlerhafte Daten ohne Versandkosten zu verhindern.")
        sys.exit(1)


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
        print(f"   ⚠️ FlareSolverr meldet Status: {data.get('message')}")
        return None
    except Exception as e:
        print(f"   ❌ Verbindungsfehler zu FlareSolverr: {e}")
        return None


def calc_mean(prices):
    if not prices:
        return None
    return round(sum(prices) / len(prices), 2)


def send_email_alert(subject, body):
    if not (SMTP_USER and SMTP_PASS and ALERT_EMAIL_TO):
        print("ℹ️ Keine SMTP-Zugangsdaten hinterlegt, überspringe E-Mail-Alarm.")
        return

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL_TO

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        print(f"📧 Warn-E-Mail erfolgreich an {ALERT_EMAIL_TO} gesendet!")
    except Exception as e:
        print(f"⚠️ Fehler beim Senden der Warn-E-Mail: {e}")


def verify_scraped_data_quality(latest_rows):
    corrupted_items = []

    for row in latest_rows:
        item_price = row.get("cheapest_item_1")
        ship_price = row.get("cheapest_ship_1")
        name = row.get("product_name")

        if item_price is not None and ship_price is not None:
            if item_price == ship_price:
                corrupted_items.append(f"- {name}: Artikelpreis = {item_price} €, Gesamtpreis = {ship_price} €")

    if corrupted_items:
        print(f"⚠️ DATENQUALITÄTS-WARNUNG: Bei {len(corrupted_items)} Artikeln fehlen die Versandkosten!")
        subject = f"🚨 GiG Alert: Fehlende Versandkosten beim Scrape ({len(corrupted_items)} Artikel)"
        body = (
            "Hallo,\n\n"
            "beim letzten Scrape-Durchlauf wurden für folgende Artikel keine Versandkosten addiert "
            "(cheapest_item_1 == cheapest_ship_1):\n\n"
            + "\n".join(corrupted_items)
            + "\n\nBitte prüfe die Login-Session oder Cardmarket-Selektoren.\n\n"
            "Dein GiG-Tracker"
        )
        send_email_alert(subject, body)
    else:
        print("✅ Datenqualitäts-Check bestanden: Versandkosten wurden überall korrekt erfasst.")


def send_telegram_alert(chat_id, message):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Fehler beim Senden des Telegram-Alerts: {e}")


def check_and_trigger_alerts(latest_results, config_products):
    if not TELEGRAM_BOT_TOKEN:
        return

    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}"
    }
    
    url = f"{SUPABASE_URL}/rest/v1/price_alerts?is_active=eq.true"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return
        alerts = resp.json()
    except Exception:
        return

    if not alerts:
        return

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    for a in alerts:
        p_name = a.get("product_name")
        chat_id = a.get("telegram_chat_id")
        metric_key = a.get("metric_type", "avg_robust_shipping")
        target_price = float(a.get("target_price", 0))
        condition = a.get("trigger_condition", "below")
        valid_until = a.get("valid_until", "")

        if valid_until and valid_until < today_str:
            continue

        res_match = next((r for r in latest_results if r["product_name"] == p_name), None)
        if not res_match:
            continue

        current_val = res_match.get(metric_key)
        if current_val is None:
            continue

        is_triggered = False
        if condition == "below" and current_val <= target_price:
            is_triggered = True
        elif condition == "above" and current_val >= target_price:
            is_triggered = True

        if is_triggered:
            conf_item = next((p for p in config_products if p["name"] == p_name), None)
            cm_url = conf_item.get("url", "https://www.cardmarket.com") if conf_item else "https://www.cardmarket.com"

            metric_names = {
                "avg_robust_shipping": "Ø Robust (inkl. Versand)",
                "avg_market_shipping": "Ø Markt (inkl. Versand)",
                "avg_robust": "Ø Robust (ohne Versand)",
                "avg_market": "Ø Markt (ohne Versand)",
                "cheapest_1": "Günstigstes Angebot (inkl. Versand)"
            }
            m_label = metric_names.get(metric_key, metric_key)
            cond_sym = "≤" if condition == "below" else "≥"

            msg = (
                f"🚨 <b>PREIS-ALERT: {p_name}</b>\n\n"
                f"🎯 <b>Zielbedingung:</b> {cond_sym} {target_price:.2f} €\n"
                f"📉 <b>Aktueller Kurs ({m_label}):</b> <b>{current_val:.2f} €</b>\n\n"
                f"🛒 <a href='{cm_url}'>Jetzt auf Cardmarket ansehen</a>"
            )

            print(f"   🚀 Trigger für {p_name} an Telegram-Chat {chat_id}!")
            send_telegram_alert(chat_id, msg)


def run_scraper():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ config.json nicht gefunden: {CONFIG_PATH}")
        return

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 1. FlareSolverr Session initialisieren & Login erzwingen
    init_flaresolverr_session()
    login_and_verify()

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    results = []
    products = config.get("products", [])

    # 2. Artikel durchgehen
    for item in products:
        p_name = item["name"]
        p_type = item.get("type", "single").lower()
        p_url = item["url"]

        print(f"\n==========================================")
        print(f"Scrape: {p_name} (Typ: {p_type})")

        html = fetch_html_via_flaresolverr(p_url)
        if not html:
            print(f"⚠️ Konnte {p_name} nicht abrufen.")
            continue

        soup = BeautifulSoup(html, "html.parser")

        # Verfügbare Artikel
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

        # Angebote sauber & eindeutig auslesen
        offer_rows = soup.select("div[id^='articleRow'], .article-row")
        parsed_item_prices = []
        parsed_total_prices = []
        seen_row_ids = set()

        for row in offer_rows:
            row_id = row.get("id")
            if row_id and row_id in seen_row_ids:
                continue
            if row_id:
                seen_row_ids.add(row_id)

            price_cell = row.select_one(".col-price, .price-container, .font-weight-bold")
            text_to_search = price_cell.get_text(" ", strip=True) if price_cell else row.get_text(" ", strip=True)
            
            euro_matches = re.findall(r"(\d+(?:\.\d{3})*,\d{2})\s*€", text_to_search)
            
            if euro_matches:
                item_price = parse_price(euro_matches[0])
                if item_price and item_price > 1.0:
                    shipping_cost = 0.0
                    
                    # 1. Priorität: Zweiter Euro-Betrag in der Preis-Spalte
                    if len(euro_matches) > 1:
                        parsed_ship = parse_price(euro_matches[1])
                        if parsed_ship is not None:
                            shipping_cost = parsed_ship
                    else:
                        # 2. Priorität: Suche im gesamten Zeilentext nach "+ X,XX €"
                        row_full_text = row.get_text()
                        ship_match = re.search(r'\+\s*([\d\.]+,\d{2})\s*€', row_full_text)
                        if ship_match:
                            parsed_ship = parse_price(ship_match.group(1))
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

            print(f"   Top 3 Gesamt: [{c1_total}€, {c2_total}€, {c3_total}€] | Robust={avg_robust_shipping}€ | Markt={avg_market_shipping}€")

            row_data = {
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
            }

            # Top 30 inkl. Versand
            for i in range(1, 31):
                row_data[f"cheapest_ship_{i}"] = sorted_total_prices[i - 1] if len(sorted_total_prices) >= i else None

            # Top 30 exkl. Versand
            for i in range(1, 31):
                row_data[f"cheapest_item_{i}"] = sorted_item_prices[i - 1] if len(sorted_item_prices) >= i else None

            results.append(row_data)

    os.makedirs("data", exist_ok=True)
    if results:
        df_new = pd.DataFrame(results)
        if os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > 0:
            df_existing = pd.read_csv(CSV_PATH)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(CSV_PATH, index=False)
        else:
            df_new.to_csv(CSV_PATH, index=False)
        print("\n=> data/data.csv wurde erfolgreich mit 30 Rängen aktualisiert!")

        # 1. Datenqualität prüfen & E-Mail senden, falls Versandkosten fehlen
        verify_scraped_data_quality(results)

        # 2. Reguläre Telegram Preis-Alerts triggern
        check_and_trigger_alerts(results, products)


if __name__ == "__main__":
    run_scraper()
