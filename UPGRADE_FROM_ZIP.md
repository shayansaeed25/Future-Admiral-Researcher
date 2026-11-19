# Install this full source update

This ZIP contains the complete project files. It deliberately excludes the local `.git` folder and private `.env` credentials so that your GitHub Desktop connection and existing secrets are not replaced.

1. Make a backup copy of your current `Future-Admiral-Researcher` folder.
2. Extract this ZIP **into the root of your existing local `Future-Admiral-Researcher` repository** (the folder that contains `.git` and the current `admiral_core.py`).
3. If Windows asks whether to replace files, choose **Replace the files in the destination**. Do not delete `.git` or your local `.env` file.
4. Open GitHub Desktop. The updated project files should appear under **Changes**. Review, commit, then push to GitHub.
5. Run the workflow once. It will update the Google Sheets `Signals` header and start filling the new regime columns. Older rows remain where they are; new columns are blank for historical rows because those readings were not recorded at the time.

## Secrets

- Do not remove or recreate the existing Gemini, Groq, OpenRouter, Google Sheets, or Discord secrets. This update does not change AI key rotation or require their values in project files.
- No new API secret is required for the public daily ETF fallback on BTC, ETH, SOL and HYP.
- `CRYPTOETF_API_KEY` remains optional. If configured, CryptoETF.today is tried first and can provide its wider asset list; if absent or unavailable, BTC/ETH/SOL/HYP fall back to Farside's public table.

## What the new fields mean

- **ETF flow:** reported daily fund-product inflows/outflows in USD millions; delayed and not a live whale-wallet feed.
- **Trend/momentum/A-D:** deterministic 1H/4H/1D OHLCV context using SMA, RSI, CMF and OBV. These are estimates, not verified institution identity.
- **Taker/order-book:** OKX venue-specific public-market proxies, not proof that a whale placed an order.
- **Trade decision:** the existing research and derivatives confirmation rules still decide BUY/SELL/NO SIGNAL. The new context does not force a trade or promise a profit.

## Candle-symbol fix applies to every coin

The fixed symbol normalizer is shared by the automatic watchlist and on-demand analysis; it is not a SUI-only special case. Any supported delimited USD, USDT, or USDC coin pair (for example `BTC-USDT`, `SUI-USDT`, `TAO-USD`, or `LINK/USDC`) is converted to the correct base symbol. OKX candle lookup tries that coin's USDT perpetual first and its USDT spot market second. If neither market/source returns candles, the card now includes the actual feed error so the missing timeframe is diagnosable.
