import requests


def get_institutional_crypto_metrics(ticker: str) -> dict:
    """
    Fetches real-time institutional metrics:
    - Coinalyze/Binance Funding Rates & Predicted Funding
    - Coinglass Liquidation Zones / Open Interest Shifts
    - Whale Aggressive Market Orders (Taker CVD)
    - Orderbook Depth Imbalance (Bids vs Asks)
    """
    base = ticker.split("-")[0].upper()
    symbol = f"{base}USDT"

    metrics = {
        "symbol": symbol,
        "funding_rate": "N/A",
        "open_interest_usd": "N/A",
        "taker_flow_cvd": "Neutral",
        "liquidation_risk": "Moderate",
        "whale_activity": "Normal",
        "large_orders": "N/A",
        "status": "success"
    }

    try:
        # 1. Real-Time Funding Rate & Mark Price (Coinalyze / Binance API)
        funding_res = requests.get(
            f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
            timeout=5
        ).json()
        if "lastFundingRate" in funding_res:
            rate = float(funding_res["lastFundingRate"]) * 100
            metrics["funding_rate"] = f"{rate:.4f}%"
            if rate > 0.05:
                metrics["liquidation_risk"] = "High Long Squeeze Risk (Over-leveraged Longs)"
            elif rate < -0.02:
                metrics["liquidation_risk"] = "High Short Squeeze Risk (Over-leveraged Shorts)"

        # 2. Institutional Open Interest (Binance Futures)
        oi_res = requests.get(
            f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
            timeout=5
        ).json()
        if "openInterest" in oi_res:
            mark_price = float(funding_res.get("markPrice", 1))
            oi_usd = float(oi_res["openInterest"]) * mark_price
            metrics["open_interest_usd"] = f"${oi_usd:,.2f}"

        # 3. Aggressive Whale Flow / Taker CVD (Cryptometer / Binance Proxy)
        taker_res = requests.get(
            f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=1h&limit=1",
            timeout=5
        ).json()
        if isinstance(taker_res, list) and len(taker_res) > 0:
            ratio = float(taker_res[0]["buySellRatio"])
            if ratio >= 1.25:
                metrics["taker_flow_cvd"] = f"Aggressive Institutional Buying (Ratio: {ratio:.2f})"
                metrics["whale_activity"] = "Heavy Spot/Futures Absorption on Bids"
            elif ratio <= 0.85:
                metrics["taker_flow_cvd"] = f"Aggressive Institutional Selling (Ratio: {ratio:.2f})"
                metrics["whale_activity"] = "Heavy Market Dumping on Asks"
            else:
                metrics["taker_flow_cvd"] = f"Balanced/Ranging CVD (Ratio: {ratio:.2f})"

        # 4. Large Orderbook Depth Imbalance (Coinglass Orderbook Proxy)
        depth_res = requests.get(
            f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=50",
            timeout=5
        ).json()
        if "bids" in depth_res and "asks" in depth_res:
            bids = [(float(p), float(q)) for p, q in depth_res["bids"]]
            asks = [(float(p), float(q)) for p, q in depth_res["asks"]]
            total_bid_vol = sum(q for _, q in bids)
            total_ask_vol = sum(q for _, q in asks)
            imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol) * 100
            metrics["orderbook_imbalance"] = f"{imbalance:+.2f}% ({'Bids Wall / Support' if imbalance > 0 else 'Asks Wall / Resistance'})"

            # 5. Large single-level orders ("whale walls"): any book level whose
            # size is a big multiple of the average level size on its side,
            # separate from the aggregate imbalance above.
            avg_bid = total_bid_vol / len(bids) if bids else 0
            avg_ask = total_ask_vol / len(asks) if asks else 0
            wall_mult = 5
            big_bid = max(bids, key=lambda x: x[1]) if bids else None
            big_ask = max(asks, key=lambda x: x[1]) if asks else None
            walls = []
            if big_bid and avg_bid and big_bid[1] >= wall_mult * avg_bid:
                walls.append(f"Large buy wall @ {big_bid[0]:,.4f} ({big_bid[1]:,.2f} {base})")
            if big_ask and avg_ask and big_ask[1] >= wall_mult * avg_ask:
                walls.append(f"Large sell wall @ {big_ask[0]:,.4f} ({big_ask[1]:,.2f} {base})")
            metrics["large_orders"] = "; ".join(walls) if walls else "No standout single-level walls in top 50"

    except Exception as e:
        metrics["status"] = f"Error: {e}"

    return metrics
