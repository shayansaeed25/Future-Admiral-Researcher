import logging
from datetime import datetime
from typing import Annotated

import pandas as pd
import requests
import yfinance as yf
from dateutil.relativedelta import relativedelta

from .binance_gate import is_binance_blocked, mark_binance_ok, note_binance_response
from .date_window import withhold_live_profile
from .market_time import format_pkt
from .stockstats_utils import (
    StockstatsUtils,
    _assert_ohlcv_not_stale,
    filter_financials_by_date,
    load_ohlcv,
    yf_retry,
)
from .symbol_utils import NoMarketDataError, normalize_symbol

logger = logging.getLogger(__name__)


def get_binance_crypto_data(symbol: str, start_date: str, end_date: str):
    """Universal live crypto candle data fetcher via Binance Public API."""
    if is_binance_blocked():
        return None
    try:
        base = symbol.split("-")[0].replace("USDT", "").replace("USD", "").upper()
        binance_symbol = f"{base}USDT"
        url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval=1d&limit=365"
        response = requests.get(url, timeout=5)
        note_binance_response(status_code=response.status_code)
        res = response.json()

        if isinstance(res, list) and len(res) > 0:
            mark_binance_ok()
            cols = [
                "Open time", "Open", "High", "Low", "Close", "Volume",
                "Close time", "Quote asset volume", "Number of trades",
                "Taker buy base", "Taker buy quote", "Ignore"
            ]
            df = pd.DataFrame(res, columns=cols)
            df["Date"] = pd.to_datetime(df["Open time"], unit="ms").dt.strftime("%Y-%m-%d")

            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df[col] = df[col].astype(float).round(4)

            # Filter requested date range
            mask = (df["Date"] >= start_date) & (df["Date"] <= end_date)
            filtered = df.loc[mask, ["Date", "Open", "High", "Low", "Close", "Volume"]]

            if not filtered.empty:
                filtered.set_index("Date", inplace=True)
                csv_string = filtered.to_csv()
                header = f"# Stock data for {symbol} (via Binance Live) from {start_date} to {end_date}\n"
                header += f"# Total records: {len(filtered)}\n"
                header += f"# Data retrieved on: {format_pkt()}\n\n"
                return header + csv_string
    except Exception as e:
        note_binance_response(exc=e)
        logger.warning("Binance crypto fetch fallback failed: %s", e)
    return None


def _crypto_df_to_csv(df: "pd.DataFrame", symbol: str, start_date: str, end_date: str, venue: str) -> str | None:
    """Shared CSV/header rendering for OKX/CoinGecko crypto OHLCV fetchers."""
    if df is None or df.empty:
        return None
    mask = (df["Date"] >= start_date) & (df["Date"] <= end_date)
    filtered = df.loc[mask, ["Date", "Open", "High", "Low", "Close", "Volume"]]
    if filtered.empty:
        return None
    filtered.set_index("Date", inplace=True)
    csv_string = filtered.to_csv()
    header = f"# Stock data for {symbol} (via {venue} Live) from {start_date} to {end_date}\n"
    header += f"# Total records: {len(filtered)}\n"
    header += f"# Data retrieved on: {format_pkt()}\n\n"
    return header + csv_string


def get_okx_crypto_data(symbol: str, start_date: str, end_date: str):
    """Crypto OHLCV via OKX's public candles endpoint (3rd source in the chain).

    OKX's daily-candle endpoint only returns a limited recent window per call,
    so history is paged backwards with `before` until the requested start
    date is covered or the venue stops returning older rows.
    """
    try:
        base = symbol.split("-")[0].replace("USDT", "").replace("USD", "").upper()
        okx_symbol = f"{base}-USDT"

        all_rows = []
        after_cursor = ""
        for _ in range(6):  # ~6*300 = up to ~1800 daily candles, plenty for 365d asks
            url = (
                "https://www.okx.com/api/v5/market/history-candles"
                f"?instId={okx_symbol}&bar=1D&limit=300"
                + (f"&after={after_cursor}" if after_cursor else "")
            )
            res = requests.get(url, timeout=5).json()
            rows = (res or {}).get("data") or []
            if not rows:
                break
            all_rows.extend(rows)
            oldest_ts = rows[-1][0]
            if oldest_ts == after_cursor:
                break
            after_cursor = oldest_ts
            oldest_date = pd.to_datetime(int(oldest_ts), unit="ms").strftime("%Y-%m-%d")
            if oldest_date <= start_date:
                break

        if not all_rows:
            return None

        # OKX candle row: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
        df = pd.DataFrame([r[:6] for r in all_rows], columns=[
            "Open time", "Open", "High", "Low", "Close", "Volume"
        ])
        df["Date"] = pd.to_datetime(df["Open time"].astype("int64"), unit="ms").dt.strftime("%Y-%m-%d")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = df[col].astype(float).round(4)
        df = df.drop_duplicates(subset="Date").sort_values("Date")

        return _crypto_df_to_csv(df, symbol, start_date, end_date, "OKX")
    except Exception as e:
        logger.warning("OKX crypto fetch fallback failed: %s", e)
    return None


def get_coingecko_crypto_data(symbol: str, start_date: str, end_date: str):
    """Crypto OHLCV via CoinGecko's public market_chart/range endpoint (last
    resort, 4th source). No OHLC on the free tier — only a daily close price
    series — so Open/High/Low are synthesized as equal to Close. That's still
    enough for RSI/MACD/Bollinger (all close-driven) when every real-OHLC
    venue above has failed for a symbol."""
    coingecko_ids = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "SUI": "sui",
        "BNB": "binancecoin", "XRP": "ripple", "LINK": "chainlink", "TAO": "bittensor",
        "AAVE": "aave", "NEAR": "near",
    }
    try:
        base = symbol.split("-")[0].replace("USDT", "").replace("USD", "").upper()
        coin_id = coingecko_ids.get(base)
        if not coin_id:
            return None

        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86400
        url = (
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
            f"?vs_currency=usd&from={start_ts}&to={end_ts}"
        )
        res = requests.get(url, timeout=8).json()
        prices = res.get("prices") or []
        if not prices:
            return None

        df = pd.DataFrame(prices, columns=["Open time", "Close"])
        df["Date"] = pd.to_datetime(df["Open time"].astype("int64"), unit="ms").dt.strftime("%Y-%m-%d")
        # Keep one row per day (CoinGecko range data can be sub-daily).
        df = df.groupby("Date", as_index=False).last()
        df["Close"] = df["Close"].astype(float).round(4)
        df["Open"] = df["High"] = df["Low"] = df["Close"]
        df["Volume"] = 0.0

        return _crypto_df_to_csv(df, symbol, start_date, end_date, "CoinGecko")
    except Exception as e:
        logger.warning("CoinGecko crypto fetch fallback failed: %s", e)
    return None


def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Crypto scans use the sources verified by the user's Actions logs:
    # OKX OHLCV, then CoinGecko's close series fallback. Binance returns 451
    # and Yahoo returns 404 for these USDT pairs, so do not waste retries.
    is_crypto = "-USD" in symbol.upper() or "USDT" in symbol.upper()
    if is_crypto:
        for fetcher in (get_okx_crypto_data, get_coingecko_crypto_data):
            data = fetcher(symbol, start_date, end_date)
            if data is not None:
                return data
        raise NoMarketDataError(
            symbol, symbol.upper(),
            "OKX and CoinGecko returned no usable crypto OHLCV data; Yahoo/Binance are disabled for this runner",
        )

    # Stocks / ETFs / Traditional symbols use Yahoo Finance.
    canonical = normalize_symbol(symbol)
    ticker = yf.Ticker(canonical)

    end_inclusive = (end_dt + relativedelta(days=1)).strftime("%Y-%m-%d")
    data = yf_retry(lambda: ticker.history(start=start_date, end=end_inclusive))

    if data.empty:
        raise NoMarketDataError(
            symbol, canonical,
            f"no rows between {start_date} and {end_date}"
            + (" (crypto: Binance/OKX/CoinGecko also returned nothing for this symbol)" if is_crypto else "")
        )

    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)

    # Staleness guard check
    _assert_ohlcv_not_stale(data, end_date, symbol, canonical)

    numeric_columns = ["Open", "High", "Low", "Close", "Adj Close"]
    for col in numeric_columns:
        if col in data.columns:
            data[col] = data[col].round(2)

    csv_string = data.to_csv()

    label = canonical if canonical == symbol.upper() else f"{canonical} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {format_pkt()}\n\n"

    return header + csv_string


def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    best_ind_params = {
        "close_50_sma": "50 SMA: A medium-term trend indicator.",
        "close_200_sma": "200 SMA: A long-term trend benchmark.",
        "close_10_ema": "10 EMA: A responsive short-term average.",
        "macd": "MACD: Computes momentum via differences of EMAs.",
        "macds": "MACD Signal: An EMA smoothing of the MACD line.",
        "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal.",
        "rsi": "RSI: Measures momentum to flag overbought/oversold conditions.",
        "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands.",
        "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above middle line.",
        "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below middle line.",
        "atr": "ATR: Averages true range to measure volatility.",
        "vwma": "VWMA: A moving average weighted by volume.",
        "mfi": "MFI: The Money Flow Index measuring buying and selling pressure.",
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)

    try:
        indicator_data = _get_stock_stats_bulk(symbol, indicator, curr_date)
        current_dt = curr_date_dt
        date_values = []

        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            if date_str in indicator_data:
                indicator_value = indicator_data[date_str]
            else:
                indicator_value = "N/A: Not a trading day (weekend or holiday)"

            date_values.append((date_str, indicator_value))
            current_dt = current_dt - relativedelta(days=1)

        ind_string = "".join(f"{date_str}: {value}\n" for date_str, value in date_values)

    except NoMarketDataError:
        raise
    except Exception as e:
        logger.warning("Bulk stockstats fetch failed, falling back per-day: %s", e)
        ind_string = ""
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        while curr_date_dt >= before:
            indicator_value = get_stockstats_indicator(
                symbol, indicator, curr_date_dt.strftime("%Y-%m-%d")
            )
            ind_string += f"{curr_date_dt.strftime('%Y-%m-%d')}: {indicator_value}\n"
            curr_date_dt = curr_date_dt - relativedelta(days=1)

    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + best_ind_params.get(indicator, "No description available.")
    )

    return result_str


def _get_stock_stats_bulk(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to calculate"],
    curr_date: Annotated[str, "current date for reference"]
) -> dict:
    from stockstats import wrap

    data = load_ohlcv(symbol, curr_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    df[indicator]

    result_dict = {}
    for _, row in df.iterrows():
        date_str = row["Date"]
        indicator_value = row[indicator]
        result_dict[date_str] = "N/A" if pd.isna(indicator_value) else str(indicator_value)

    return result_dict


def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
) -> str:
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date_dt.strftime("%Y-%m-%d")

    try:
        indicator_value = StockstatsUtils.get_stock_stats(
            symbol,
            indicator,
            curr_date,
        )
    except NoMarketDataError:
        raise
    except Exception as e:
        logger.warning("Stockstats indicator %s failed on %s: %s", indicator, curr_date, e)
        return ""

    return str(indicator_value)


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"] = None
):
    canonical = normalize_symbol(ticker)
    withheld = withhold_live_profile(curr_date, canonical)
    if withheld:
        return withheld

    try:
        ticker_obj = yf.Ticker(canonical)
        info = yf_retry(lambda: ticker_obj.info)

        if not info:
            raise NoMarketDataError(ticker, canonical, "no fundamentals returned")

        fields = [
            ("Name", info.get("longName")),
            ("Sector", info.get("sector")),
            ("Industry", info.get("industry")),
            ("Market Cap", info.get("marketCap")),
            ("PE Ratio (TTM)", info.get("trailingPE")),
            ("Forward PE", info.get("forwardPE")),
            ("Price to Book", info.get("priceToBook")),
            ("52 Week High", info.get("fiftyTwoWeekHigh")),
            ("52 Week Low", info.get("fiftyTwoWeekLow")),
            ("50 Day Average", info.get("fiftyDayAverage")),
            ("200 Day Average", info.get("twoHundredDayAverage")),
        ]

        lines = [f"{label}: {v}" for label, v in fields if v is not None]
        if not lines:
            raise NoMarketDataError(ticker, canonical, "no fundamental fields returned")

        header = f"# Company Fundamentals for {canonical}\n"
        header += f"# Data retrieved on: {format_pkt()}\n\n"
        return header + "\n".join(lines)

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None):
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.quarterly_balance_sheet if freq.lower() == "quarterly" else ticker_obj.balance_sheet)
        data = filter_financials_by_date(data, curr_date)
        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no balance sheet data")
        return f"# Balance Sheet ({freq})\n\n" + data.to_csv()
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None):
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.quarterly_cashflow if freq.lower() == "quarterly" else ticker_obj.cashflow)
        data = filter_financials_by_date(data, curr_date)
        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no cash flow data")
        return f"# Cash Flow ({freq})\n\n" + data.to_csv()
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None):
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.quarterly_income_stmt if freq.lower() == "quarterly" else ticker_obj.income_stmt)
        data = filter_financials_by_date(data, curr_date)
        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no income statement data")
        return f"# Income Statement ({freq})\n\n" + data.to_csv()
    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


def get_insider_transactions(ticker: str):
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.insider_transactions)
        if data is None or data.empty:
            return f"No insider transactions reported for symbol '{canonical}'"
        return f"# Insider Transactions for {canonical}\n\n" + data.to_csv()
    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"
