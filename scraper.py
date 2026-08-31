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

    # 1. Verfügbare Artikel ermitteln
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

    # 2. Eindeutige Artikel-Zeilen finden (verhindert 3-fache Duplikate)
    rows = soup.select('div.article-row, div[id^="articleRow"], .table-body > .row')
    if not rows:
        rows = soup.select('.table-body .row')

    seen_articles = set()
    offers = []

    for row in rows:
        # Falls eine Row-ID existiert, Duplikate verhindern
        row_id = row.get('id')
        if row_id and row_id in seen_articles:
            continue
        if row_id:
            seen_articles.add(row_id)

        # A) Artikelpreis extrahieren
        price_elem = row.select_one('.col-price .color-primary, .col-price, .price-container .color-primary, .color-primary')
        if not price_elem:
            continue
        
        item_price = clean_price(price_elem.get_text())
        if item_price is None or item_price <= 0:
            continue

        # B) Versandkosten extrahieren
        # Suche nach spezifischen Versand-Tags oder Text mit "+ X,XX €"
        shipping_price = 0.0
        
        # Selektor 1: Klassische Cardmarket-Versandcontainer
        ship_elem = row.select_one('.col-seller .small, .col-shipping, .shipping-price, span.d-none.d-md-inline')
        if ship_elem:
            ship_txt = ship_elem.get_text()
            parsed_ship = clean_price(ship_txt)
            if parsed_ship is not None:
                shipping_price = parsed_ship
        
        # Fallback: Suche per Regex im gesamten Zeilentext nach "+ X,XX €"
        if shipping_price == 0.0:
            row_text = row.get_text()
            ship_match = re.search(r'\+\s*([\d\.]+,\d{2})\s*€', row_text)
            if ship_match:
                raw_ship = ship_match.group(1).replace('.', '').replace(',', '.')
                try:
                    shipping_price = float(raw_ship)
                except ValueError:
                    pass

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

    by_shipping = sorted(offers, key=lambda x: x['total_price'])
    by_item = sorted(offers, key=lambda x: x['item_price'])

    ship_totals = [o['total_price'] for o in by_shipping]
    item_totals = [o['item_price'] for o in by_item]

    if p_type.lower() == 'case':
        robust_ship = ship_totals[1:5] if len(ship_totals) >= 5 else ship_totals[1:] if len(ship_totals) > 1 else ship_totals
        market_ship = ship_totals[:10]
        robust_item = item_totals[1:5] if len(item_totals) >= 5 else item_totals[1:] if len(item_totals) > 1 else item_totals
        market_item = item_totals[:10]
    else:
        robust_ship = ship_totals[2:10] if len(ship_totals) >= 10 else ship_totals[2:] if len(ship_totals) > 2 else ship_totals
        market_ship = ship_totals[:15]
        robust_item = item_totals[2:10] if len(item_totals) >= 10 else item_totals[2:] if len(item_totals) > 2 else item_totals
        market_item = item_totals[:15]

    metrics = {
        'avg_robust_shipping': round(sum(robust_ship) / len(robust_ship), 2) if robust_ship else None,
        'avg_market_shipping': round(sum(market_ship) / len(market_ship), 2) if market_ship else None,
        'avg_robust': round(sum(robust_item) / len(robust_item), 2) if robust_item else None,
        'avg_market': round(sum(market_item) / len(market_item), 2) if market_item else None,
        'cheapest_1': ship_totals[0] if len(ship_totals) > 0 else None,
        'cheapest_2': ship_totals[1] if len(ship_totals) > 1 else None,
        'cheapest_3': ship_totals[2] if len(ship_totals) > 2 else None,
    }

    # Top 20 Gesamtpreise (inkl. Versand)
    for i in range(1, 21):
        metrics[f'cheapest_ship_{i}'] = ship_totals[i - 1] if len(ship_totals) >= i else None

    # Top 20 Artikelpreise (exkl. Versand)
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

        if offers:
            print(f"   📊 Gefunden: {len(offers)} Angebote | Günstigster: {offers[0]['item_price']}€ + {offers[0]['shipping_price']}€ Versand = {offers[0]['total_price']}€")

        row = {
            'timestamp': now_str,
            'product_name': name,
            'product_type': p_type,
            'available_items': available_items,
            **metrics
        }
        new_rows.append(row)
        time.sleep(2)

    if not new_rows:
        print("⚠️ Keine neuen Daten gesammelt.")
        return

    os.makedirs(os.path.dirname(DATA_CSV_PATH), exist_ok=True)
    df_new = pd.DataFrame(new_rows)

    if os.path.exists(DATA_CSV_PATH):
        df_existing = pd.read_csv(DATA_CSV_PATH)
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_combined = df_new

    df_combined.to_csv(DATA_CSV_PATH, index=False, encoding='utf-8')
    print(f"✅ Erfolgreich {len(new_rows)} Produkte sauber in {DATA_CSV_PATH} gespeichert.")


if __name__ == '__main__':
    main()
