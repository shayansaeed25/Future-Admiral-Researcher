# $100 paper-trade ledger

Every new BUY/SELL research signal opens a simulated position with exactly 100 USDT notional. This is a paper-tracking amount; the bot does not place an exchange order.

The paper quantity is `100 / entry price`. It is split into three equal quantity tranches: one exits at each of TP1, TP2 and TP3. Any quantity still open when SL is reached exits at the stop. If one observed candle range crosses both SL and a target, the existing conservative stop-first rule is used because OHLC candles cannot prove which level traded first.

Google Sheets Signals records each analysis outcome, including NO_SIGNAL and ANALYSIS_ERROR. Positions holds each BUY/SELL paper position and its target progress, remaining quantity, realized/unrealized/total gross P&L, and return. The Dashboard summarizes these values.

P&L is a mark/touch-based estimate from public price candles. It excludes fees, spread, slippage, funding, taxes, partial exchange fills, and execution latency. It is not realized exchange P&L and does not guarantee future performance. Existing trades are not backfilled as historical paper trades unless their signal/entry history is present in the ledger.

The research signal still requires both the AI desk direction and deterministic derivatives matrix direction to agree. Missing data or disagreement remains NO_SIGNAL; thresholds are not loosened just to manufacture trades.
