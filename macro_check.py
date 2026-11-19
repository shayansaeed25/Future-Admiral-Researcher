"""Quick diagnostic: which macro data sources are reachable from THIS machine (GitHub runner)?

    python macro_check.py
Prints one line per source: OK / FAIL, seconds, and the reason. Send me the output if macro data is missing.
"""
import os
import time

import requests

import macro

TESTS = {
    "Yahoo ^TNX (US10Y)": lambda: macro.parse_yahoo_chart(requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=3mo&interval=1d", timeout=10, headers={"User-Agent": macro.BROWSER_UA}).json()),
    "Yahoo DX-Y.NYB (dollar)": lambda: macro.parse_yahoo_chart(requests.get("https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=3mo&interval=1d", timeout=10, headers={"User-Agent": macro.BROWSER_UA}).json()),
    "Yahoo CL=F (oil)": lambda: macro.parse_yahoo_chart(requests.get("https://query1.finance.yahoo.com/v8/finance/chart/CL%3DF?range=3mo&interval=1d", timeout=10, headers={"User-Agent": macro.BROWSER_UA}).json()),
    "FRED csv DGS10": lambda: macro.parse_fred_csv(macro._get(macro.FRED_CSV.format("DGS10"), timeout=15, tries=1)),
    "FRED csv T10YIE": lambda: macro.parse_fred_csv(macro._get(macro.FRED_CSV.format("T10YIE"), timeout=15, tries=1)),
    "Fear&Greed": lambda: [float(requests.get("https://api.alternative.me/fng/?limit=1", timeout=10).json()["data"][0]["value"])],
    "CryptoETF": lambda: [1.0] if requests.get("https://cryptoetf.today", timeout=10).status_code < 400 else [],
    "Fed RSS": lambda: [1.0] if macro.get_headlines(1) else [],
}
if os.getenv("FRED_API_KEY"):
    key = os.environ["FRED_API_KEY"]
    TESTS["FRED api DGS10"] = lambda: macro.parse_fred_api(requests.get(f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key={key}&file_type=json&sort_order=desc&limit=10", timeout=10).json())

for name, fn in TESTS.items():
    t = time.time()
    try:
        vals = fn()
        print(f"{'OK  ' if vals else 'EMPTY'} {name:28} {time.time() - t:5.1f}s  last={vals[-1] if vals else '-'}")
    except Exception as exc:
        print(f"FAIL {name:28} {time.time() - t:5.1f}s  {type(exc).__name__}: {str(exc)[:90]}")
