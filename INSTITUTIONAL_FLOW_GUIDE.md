# Institutional-flow context

## What is added

- Crypto analysis no longer selects the retail social-media analyst.
- The signal card and full report show daily spot-ETF flow context for 13 assets.
- The `Signals` tab logs latest-day flow, recent seven-day net flow, bias, source date, and source.
- Missing credentials, unsupported symbols, stale data, and API failures are labeled; they do not stop the analysis.
- BTC, ETH, SOL and HYP also use Farside Investors' published daily totals as a no-key fallback when the optional API key is absent or its request fails. The current session's incomplete all-dash row is ignored.
- The Discord card and report show a separate 1H/4H/1D market-regime panel: trend, momentum, and an OHLCV-based accumulation/distribution estimate. Signals also log these fields plus OKX taker-flow and order-book proxies in Google Sheets.

## Free setup

No new secret is required for the public Farside fallback on BTC, ETH, SOL and HYP. To use CryptoETF.today as the primary source and enable its broader 13-asset coverage, create its free API key at https://cryptoetf.today and add this GitHub Actions repository secret:

| Secret | Value |
| --- | --- |
| `CRYPTOETF_API_KEY` | The key shown in your CryptoETF.today profile |

The workflow injects it automatically. You do not have to edit files after adding the secret. If it is absent, BTC/ETH/SOL/HYP use the public Farside fallback; other assets without public coverage remain explicitly unavailable.

## Important data limits

ETF fund flows are daily and can arrive with a delay. Farside publishes daily totals in US$ millions for the supported products. Positive net flow means reported net inflow into those products; it does not prove that a particular whale wallet bought coins, that buying is occurring live, or that a specific institution initiated the flow. CryptoETF.today's broader asset series also may include aggregator-based/provider-computed values. Treat ETF flow as one confluence input, not a price target.

Exchange order-book imbalance, taker flow, funding, and open interest already present in the bot are market/derivatives proxies. The new accumulation/distribution label is derived from candle-based CMF/OBV; it is not a wallet-flow feed. None of these can reliably identify the beneficial owner as an institution. This release displays ETF flows alongside those proxies but does not change the existing weighted-score thresholds or make ETF flow alone trigger a trade. Keep recording paper outcomes before changing factor weights or thresholds.

The existing news analyst remains active. News can describe institutional or corporate activity; it is not treated as proof that a trade should be opened without agreement from the existing decision gates.
