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

    # Prüfen, ob bereits eingeloggt oder Login-Formular vorhanden
    if page.locator("a[href*='/User/'], .user-nav, .account-button").count() > 0:
        print("✅ Bereits eingeloggt!")
        return True

    try:
        # Klick auf Login-Button / Dropdown falls nötig
        login_link = page.locator("a[href*='/Login'], button:has-text('Anmelden'), a:has-text('Anmelden')").first
        if login_link.count() > 0 and login_link.is_visible():
            login_link.click()
            time.sleep(2)

        # Benutzername und Passwort in Input-Felder tippen
        user_input = page.locator("input[name='username'], input[name='_username'], input[id='username']").first
        pass_input = page.locator("input[name='userPassword'], input[name='password'], input[name='_password'], input[type='password']").first

        if user_input.count() > 0 and pass_input.count() > 0:
            user_input.fill(CM_USERNAME)
            pass_input.fill(CM_PASSWORD)
            
            # Formular absenden
            submit_btn = page.locator("input[type='submit'], button[type='submit'], input[value='Anmelden']").first
            if submit_btn.count() > 0:
                submit_btn.click()
            else:
                pass_input.press("Enter")

            page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(4)

            # Prüfen, ob Login geglückt ist
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
    withIt looks like we are starting a fresh conversation without the previous context. 

To help you with **Option A**, could you share what you're working on or paste the choices/task you're referring to? For example, is this:

* A multiple-choice question, test preparation, or problem set?
* A specific draft, strategy, or plan we were refining?
* A decision between different workflows, formulas, or project approaches?
