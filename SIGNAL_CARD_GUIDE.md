# Future Admiral Discord card

## Trade signal

- The card leads with the USDT pair and final BUY/SELL decision.
- Evidence score is shown by itself on the card; coverage counts and per-feed diagnostics stay in the local health log and System Health sheet.
- Entry, TP1/TP2/TP3, stop-loss, expected TP1 move, reward-to-risk, and a 100 USDT notional example are grouped into short fields.
- The research summary is capped to a short preview. The full report link stays on the card.
- Timestamps use Pakistan Standard Time (PKT).

## No signal

NO SIGNAL cards show the evidence score and a brief status. They do not show a zero stop-loss, unused targets, or the long research summary. The full report link is still included when available.

## Estimated liquidity zones

Above/below zones come from repeated swing-pivot clusters in 1h, 4h, and 1d OHLCV candles. They are rough stop/liquidation-pool proxies, not live exchange liquidation levels or a heatmap. Binance candle data falls back to OKX where available. The card labels these zones as estimates and does not imply price must reach them.

## Market regime and flow context

The card also shows overall and 1h/4h/1d trend, momentum, and an accumulation/distribution estimate computed from SMA, RSI, Chaikin Money Flow (CMF), and On-Balance Volume (OBV) on public OHLCV candles. OKX taker buy/sell and top-20 order-book balance are labeled venue-scoped proxies. They do not identify whale wallets or prove institutional activity. Daily ETF flows are reported separately; for BTC/ETH/SOL/HYP, the public Farside table is used when the optional keyed ETF source is unavailable. These context fields do not bypass the existing confirmation/decision gates.
