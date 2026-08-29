name: TCG Price Scraper

on:
  schedule:
    - cron: '0 8,20 * * *'
  workflow_dispatch:

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Setup Tor
        run: |
          sudo apt-get update
          sudo apt-get install -y tor
          sudo systemctl start tor

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install Dependencies
        run: |
          pip install requests[socks] beautifulsoup4 pandas streamlit plotly curl_cffi

      - name: Run Scraper
        env:
          BOTFATHER: ${{ secrets.BOTFATHER }}
          TELEGRAMCHATID: ${{ secrets.TELEGRAMCHATID }}
        run: python scraper.py

      - name: Commit and Push Data
        run: |
          git config --global user.name "github-actions[bot]"
          git config --global user.email "github-actions[bot]@users.noreply.github.com"
          git add -A
          git diff --quiet && git diff --staged --quiet || (git commit -m "Update price data [skip ci]" && git push)
