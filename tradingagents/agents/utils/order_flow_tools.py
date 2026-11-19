import requests
from typing import Annotated

def get_order_flow_metrics(
    symbol: Annotated[str, "Crypto ticker symbol like BTC-USD or SUI-USD"],
    curr_date: Annotated[str, "Current date YYYY-MM-DD"] = None
) -> str:
    """Fetches real-time institutional Spot CVD, Binance Futures Open Interest, and Funding Rate."""
    base = symbol.split("-")[0].replace("USDT", "").replace("USD", "").upper()
    fapi_symbol = f"{base}USDT"

    report = [f"# Real-Time Institutional Order Flow: {fapi_symbol}"]
    try:
        # 1. Funding Rate
        fr = requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={fapi_symbol}", timeout=5).json()
        if "lastFundingRate" in fr:
            rate = float(fr["lastFundingRate"]) * 100
            report.append(f"- Current Funding Rate: {rate:.4f}%")
            if rate > 0.03:
                report.append("  * Bias: Overleveraged Longs (Long Squeeze vulnerability)")
            elif rate < -0.01:
                report.append("  * Bias: Heavily Shorted (Short Squeeze potential)")
            else:
                report.append("  * Bias: Neutral funding environment")

        # 2. Open Interest
        oi = requests.get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={fapi_symbol}", timeout=5).json()
        if "openInterest" in oi and "markPrice" in fr:
            oi_usd = float(oi["openInterest"]) * float(fr["markPrice"])
            report.append(f"- Open Interest: ${oi_usd:,.2f} USD")

        # 3. Taker Volume / Spot CVD Bias
        ls = requests.get(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={fapi_symbol}&period=1h&limit=1", timeout=5).json()
        if isinstance(ls, list) and len(ls) > 0:
            ratio = float(ls[0]["buySellRatio"])
            report.append(f"- Aggressive Taker Flow (1h Ratio): {ratio:.2f}")
            if ratio > 1.15:
                report.append("  * CVD: Institutional market absorption on Bids (Aggressive Buying)")
            elif ratio < 0.85:
                report.append("  * CVD: Institutional market dumping on Asks (Aggressive Selling)")
            else:
                report.append("  * CVD: Balanced market orders")

        # 4. Order Book Depth
        depth = requests.get(f"https://fapi.binance.com/fapi/v1/depth?symbol={fapi_symbol}&limit=50", timeout=5).json()
        if "bids" in depth and "asks" in depth:
            bids = sum(float(b[1]) for b in depth["bids"])
            asks = sum(float(a[1]) for a in depth["asks"])
            imbalance = ((bids - asks) / (bids + asks)) * 100
            report.append(f"- Top 50 Order Book Imbalance: {imbalance:+.2f}% ({'Support Wall' if imbalance > 0 else 'Resistance Wall'})")

    except Exception as e:
        report.append(f"- Order Flow Feed Notice: Live socket unavailable ({e})")

    return "\n".join(report)