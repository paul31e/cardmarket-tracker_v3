import json
import os
import re
import time
from datetime import datetime
import pandas as pd
import requests
from bs4 import BeautifulSoup

CONFIG_PATH = 'config.json'
DATA_CSV_PATH = 'data/data.csv'
FLARESOLVERR_URL = os.environ.get('FLARESOLVERR_URL', 'http://localhost:8191/v1')


def clean_price(text):
    if not text:
        return None
    # Filtert Zahlen im deutschen Format (z.B. "1.234,50 €" -> 1234.50)
    match = re.search(r'([\d\.]+,\d{2})', text)
    if match:
        raw = match.group(1).replace('.', '').replace(',', '.')
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def parse_cardmarket_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')

    # 1. Verfügbare Gesamtartikel ermitteln
    available_items = None
    avail_elem = soup.find(string=re.compile(r'Verfügbare Artikel|Available items', re.I))
    if avail_elem:
        parent = avail_elem.find_parent()
        if parent:
            text = parent.get_text()
            match = re.search(r'(\d+[\d\.]*)', text.replace(avail_elem, ''))
            if match:
                try:
                    available_items = int(match.group(1).replace('.', ''))
                except ValueError:
                    pass

    # Fallback für verfügbare Artikel über Info-Block
    if available_items is None:
        info_boxes = soup.select('.info-list-container dd, .labeled')
        for box in info_boxes:
            txt = box.get_text()
            if re.search(r'^\d+[\d\.]*$', txt.strip()):
                try:
                    available_items = int(txt.strip().replace('.', ''))
                    break
                except ValueError:
                    continue

    # 2. Tabellenzeilen / Artikelangebote parsen
    rows = soup.select('.table-body .row, .article-row')
    offers = []

    for row in rows:
        # Preis des Artikels
        price_elem = row.select_one('.color-primary, .price-container, .col-price')
        item_price = clean_price(price_elem.get_text()) if price_elem else None

        if item_price is None:
            continue

        # Versandkosten ermitteln (Standard: 0.0 wenn nicht ersichtlich / Abholung)
        shipping_elem = row.select_one('.col-shipping, .shipping-price, .d-none.d-md-inline')
        shipping_price = 0.0
        if shipping_elem:
            parsed_ship = clean_price(shipping_elem.get_text())
            if parsed_ship is not None:
                shipping_price = parsed_ship

        total_price = round(item_price + shipping_price, 2)
        offers.append({
            'item_price': item_price,
            'shipping_price': shipping_price,
            'total_price': total_price
        })

    return available_items, offers


def fetch_with_flaresolverr(url):
    payload = {
        'cmd': 'request.get',
        'url': url,
        'maxTimeout': 60000
    }
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(FLARESOLVERR_URL, json=payload, headers=headers, timeout=70)
        res_json = response.json()
        if res_json.get('status') == 'ok':
            return res_json.get('solution', {}).get('response')
        else:
            print(f"⚠️ FlareSolverr Fehler: {res_json.get('message')}")
            return None
    except Exception as e:
        print(f"❌ FlareSolverr Request fehlgeschlagen für {url}: {e}")
        return None


def calculate_metrics(offers, p_type='single'):
    if not offers:
        return {}

    # Sortiert nach Gesamtpreis inkl. Versand
    by_shipping = sorted(offers, key=lambda x: x['total_price'])
    # Sortiert nach reinem Artikelpreis ohne Versand
    by_item = sorted(offers, key=lambda x: x['item_price'])

    ship_totals = [o['total_price'] for o in by_shipping]
    item_totals = [o['item_price'] for o in by_item]

    # Dynamische Durchschnitte je nach Produkttyp (Single vs Case)
    if p_type.lower() == 'case':
        # Cases haben weniger Angebote: Top 2-5 bzw. Top 10
        robust_ship = ship_totals[1:5] if len(ship_totals) >= 5 else ship_totals[1:] if len(ship_totals) > 1 else ship_totals
        market_ship = ship_totals[:10]
        robust_item = item_totals[1:5] if len(item_totals) >= 5 else item_totals[1:] if len(item_totals) > 1 else item_totals
        market_item = item_totals[:10]
    else:
        # Singles: Top 3-10 bzw. Top 15
        robust_ship = ship_totals[2:10] if len(ship_totals) >= 10 else ship_totals[2:] if len(ship_totals) > 2 else ship_totals
        market_ship = ship_totals[:15]
        robust_item = item_totals[2:10] if len(item_totals) >= 10 else item_totals[2:] if len(item_totals) > 2 else item_totals
        market_item = item_totals[:15]

    metrics = {
        'avg_robust_shipping': round(sum(robust_ship) / len(robust_ship), 2) if robust_ship else None,
        'avg_market_shipping': round(sum(market_ship) / len(market_ship), 2) if market_ship else None,
        'avg_robust': round(sum(robust_item) / len(robust_item), 2) if robust_item else None,
        'avg_market': round(sum(market_item) / len(market_item), 2) if market_item else None,
        # Alte Legacy-Keys zur Sicherheit weiterführen
        'cheapest_1': ship_totals[0] if len(ship_totals) > 0 else None,
        'cheapest_2': ship_totals[1] if len(ship_totals) > 1 else None,
        'cheapest_3': ship_totals[2] if len(ship_totals) > 2 else None,
    }

    # Top 20 Einzelpreise inkl. Versand
    for i in range(1, 21):
        metrics[f'cheapest_ship_{i}'] = ship_totals[i - 1] if len(ship_totals) >= i else None

    # Top 20 Einzelpreise exkl. Versand
    for i in range(1, 21):
        metrics[f'cheapest_item_{i}'] = item_totals[i - 1] if len(item_totals) >= i else None

    return metrics


def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Konfigurationsdatei nicht gefunden: {CONFIG_PATH}")
        return

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)

    products = config.get('products', [])
    if not products:
        print("ℹ️ Keine Produkte in config.json definiert.")
        return

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    new_rows = []

    for p in products:
        name = p.get('name')
        url = p.get('url')
        p_type = p.get('type', 'single')

        print(f"🔍 Scrape {name} ({p_type})...")
        html = fetch_with_flaresolverr(url)
        
        if not html:
            print(f"⚠️ Überspringe {name}, da kein HTML geladen werden konnte.")
            continue

        available_items, offers = parse_cardmarket_html(html)
        metrics = calculate_metrics(offers, p_type)

        row = {
            'timestamp': now_str,
            'product_name': name,
            'product_type': p_type,
            'available_items': available_items,
            **metrics
        }
        new_rows.append(row)
        time.sleep(2)  # Kurze Pause zwischen Anfragen

    if not new_rows:
        print("⚠️ Keine neuen Daten gesammelt.")
        return

    # In CSV anhängen
    os.makedirs(os.path.dirname(DATA_CSV_PATH), exist_ok=True)
    df_new = pd.DataFrame(new_rows)

    if os.path.exists(DATA_CSV_PATH):
        df_existing = pd.read_csv(DATA_CSV_PATH)
        df_combined = pd.concat([df_existing, df_new], ignore_axis=0)
    else:
        df_combined = df_new

    df_combined.to_csv(DATA_CSV_PATH, index=False, encoding='utf-8')
    print(f"✅ Erfolgreich {len(new_rows)} Produkte mit Top-20-Preisen in {DATA_CSV_PATH} gespeichert.")


if __name__ == '__main__':
    main()
