"""Walk-forward backtest of the exact live engine + portfolio rules.

    python backtest.py                       # 180 days, default coins
    python backtest.py --days 365 --fee-bps 8
    python backtest.py --coins BTC,ETH,SOL --min-score 58

Run it BEFORE trusting the bot with real money (or use the "Future Admiral
Backtest" GitHub workflow, where OKX is reachable).  Notes on honesty:

* Only the 1H history is downloaded; 4H and 1D bars are built from it, using
  only COMPLETED higher-timeframe bars (no look-ahead).
* Funding / open-interest history is not replayed (those score components stay
  neutral), so live scores can differ slightly from backtest scores.
* Intrabar order is unknown: if stop and target are both inside one 1H bar the
  stop is assumed first (same conservative rule as the live monitor).
* Costs are charged per round trip (``--fee-bps`` per side incl. slippage).
"""
from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

import signal_engine as se
from position_state import evaluate_position_range

DEFAULT_COINS = "BTC ETH SOL XRP BNB DOGE ADA AVAX LINK DOT LTC BCH TRX NEAR APT ARB OP ATOM UNI SUI".split()
HOUR_MS = 3_600_000


# ------------------------------------------------------------------ data
def fetch_history(ticker: str, days: int) -> dict[str, np.ndarray]:
    import market_data as md
    want = days * 24 + 1500  # + warm-up so the daily trend filter is valid from day one
    rows: list = []
    after = None
    while len(rows) < want:
        params = {"instId": md.inst_id(ticker), "bar": "1H", "limit": 100}
        if after:
            params["after"] = after
        batch = md._get("/api/v5/market/history-candles", params)
        if not batch:
            break
        rows.extend(batch)
        after = batch[-1][0]
        if len(batch) < 100:
            break
    if not rows:
        raise RuntimeError(f"no history for {ticker}")
    rows = [r for r in rows if len(r) < 9 or str(r[8]) == "1"]
    return md._rows_to_arrays(rows[:want], closed_only=False)


def resample(d: dict[str, np.ndarray], seconds: int, offset_s: int = 0) -> dict[str, np.ndarray]:
    """Aggregate 1H bars into complete higher-timeframe bars (partial first/last bar dropped)."""
    bucket = np.floor((d["ts"] / 1000 + offset_s) / seconds).astype(np.int64)
    out = defaultdict(list)
    ids = np.unique(bucket)
    per = seconds // 3600
    for b in ids:
        m = bucket == b
        if m.sum() != per:
            continue  # incomplete bucket
        idx = np.where(m)[0]
        out["ts"].append(d["ts"][idx[0]])
        out["o"].append(d["o"][idx[0]])
        out["h"].append(d["h"][idx].max())
        out["l"].append(d["l"][idx].min())
        out["c"].append(d["c"][idx[-1]])
        out["v"].append(d["v"][idx].sum())
    return {k: np.array(v) for k, v in out.items()}


@dataclass
class CoinData:
    ticker: str
    f1: se.Frame
    f4: se.Frame
    fd: se.Frame
    end4: np.ndarray
    endd: np.ndarray
    decisions: dict = field(default_factory=dict)


def prepare(ticker: str, hist: dict[str, np.ndarray]) -> CoinData:
    c4 = resample(hist, 4 * 3600, 0)
    cd = resample(hist, 86400, 8 * 3600)  # OKX daily candles open at 00:00 UTC+8
    return CoinData(ticker, se.build_frame(hist), se.build_frame(c4), se.build_frame(cd),
                    c4["ts"] + 4 * HOUR_MS, cd["ts"] + 24 * HOUR_MS)


def decide(cd: CoinData, i: int, btc: CoinData | None, cfg: dict):
    """Cached engine decision for 1H bar ``i`` (min_score 0 so thresholds can be swept later)."""
    if i in cd.decisions:
        return cd.decisions[i]
    close_t = cd.f1.ts[i] + HOUR_MS
    i4 = int(np.searchsorted(cd.end4, close_t, side="right")) - 1
    idd = int(np.searchsorted(cd.endd, close_t, side="right")) - 1
    btc_trend = None
    if btc is not None and cd.ticker != btc.ticker:
        b4 = int(np.searchsorted(btc.end4, close_t, side="right")) - 1
        btc_trend = se.trend_state(btc.f4, b4) if b4 >= 55 else 0
    res = se.evaluate(cd.f1, i, cd.f4, i4, cd.fd, idd, ticker=cd.ticker, btc_trend_4h=btc_trend, cfg=cfg) \
        if (i4 >= 0 and idd >= 0) else {"action": "NO TRADE", "score": 0, "blockers": ["warmup"]}
    cd.decisions[i] = res
    return res


# ------------------------------------------------------------ simulation
def simulate(coins: dict[str, CoinData], cfg: dict, *, min_score: float, fee_bps: float, max_open: int = 4,
             max_same_dir: int = 2, cooldown_h: int = 12, pause_after_losses: int = 3, pause_h: int = 12,
             max_hold_h: int = 72, start_bars: int = 1500) -> list[dict]:
    btc = coins.get("BTC-USDT")
    all_ts = sorted({int(t) for c in coins.values() for t in c.f1.ts[start_bars:]})
    open_pos: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    trades: list[dict] = []
    paused_until = 0
    streak = 0
    ts_index = {k: {int(t): n for n, t in enumerate(c.f1.ts)} for k, c in coins.items()}

    for t in all_ts:
        # 1) manage open positions with this bar (bar [t, t+1h) is fully known at t+1h)
        for k in list(open_pos):
            n = ts_index[k].get(t)
            if n is None:
                continue
            c, p = coins[k], open_pos[k]
            if n <= p["bar"]:
                continue
            hi, lo, close = float(c.f1.h[n]), float(c.f1.l[n]), float(c.f1.c[n])
            out = evaluate_position_range(p["pos"], lo, hi)
            for stage in out["newly_hit"]:
                p["r"] += (1 / 3) * cfg[f"tp{stage}_r"]
                p["rem"] -= 1 / 3
            done, status = out["closed"], out["status"]
            if done and out["stop_hit"]:
                direction = 1 if p["pos"]["action"] == "BUY" else -1
                p["r"] += p["rem"] * (out["exit_price"] - p["pos"]["entry"]) * direction / p["risk"]
                p["rem"] = 0
            elif (n - p["bar"]) >= max_hold_h and not done:
                direction = 1 if p["pos"]["action"] == "BUY" else -1
                p["r"] += p["rem"] * (close - p["pos"]["entry"]) * direction / p["risk"]
                p["rem"], done, status = 0, True, "TIME_EXIT"
            elif done:  # TP3
                p["rem"] = 0
            if done:
                cost_r = (2 * fee_bps / 1e4) * p["pos"]["entry"] / p["risk"]
                r_net = p["r"] - cost_r
                trades.append({**p["meta"], "exit_ts": t, "status": status, "r": round(r_net, 3), "r_gross": round(p["r"], 3),
                               "hours": n - p["bar"]})
                streak = streak + 1 if status == "SL_HIT" else 0 if r_net > 0 else streak
                if status == "SL_HIT":
                    cooldown[k] = t + cooldown_h * HOUR_MS
                if streak >= pause_after_losses:
                    paused_until, streak = t + pause_h * HOUR_MS, 0
                del open_pos[k]

        # 2) new signals on bars that closed at t+1h
        if t < paused_until:
            continue
        cands = []
        for k, c in coins.items():
            n = ts_index[k].get(t)
            if n is None or n < start_bars or k in open_pos or cooldown.get(k, 0) > t:
                continue
            d = decide(c, n, btc, cfg)
            if d["action"] in ("BUY", "SELL") and d["score"] >= min_score:
                cands.append((d["score"], k, n, d))
        for score, k, n, d in sorted(cands, key=lambda x: -x[0]):
            same = sum(1 for p in open_pos.values() if p["pos"]["action"] == d["action"])
            if len(open_pos) >= max_open or same >= max_same_dir:
                continue
            lv = d["levels"]
            pos = {"action": d["action"], "entry": lv["entry"], "sl": lv["sl"], "tp1": lv["tp1"], "tp2": lv["tp2"],
                   "tp3": lv["tp3"], "tp1_status": "PENDING", "tp2_status": "PENDING", "tp3_status": "PENDING"}
            open_pos[k] = {"pos": pos, "bar": n, "risk": lv["risk"], "r": 0.0, "rem": 1.0,
                           "meta": {"ticker": k, "action": d["action"], "setup": d["setup"], "grade": d["grade"],
                                    "score": d["score"], "entry_ts": t + HOUR_MS, "entry": lv["entry"], "risk_pct": round(lv["risk_pct"], 2)}}
    return trades


# --------------------------------------------------------------- reporting
def stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t["r"] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    equity = np.cumsum(r)
    dd = float((np.maximum.accumulate(equity) - equity).max())
    return {"n": len(r), "win%": round(100 * len(wins) / len(r), 1), "avgR": round(float(r.mean()), 3),
            "totR": round(float(r.sum()), 1), "PF": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else float("inf"),
            "maxDD_R": round(dd, 1), "avg_hours": round(float(np.mean([t["hours"] for t in trades])), 1)}


def fmt_table(rows: list[tuple[str, dict]]) -> str:
    head = f"{'':<16}{'n':>5}{'win%':>7}{'avgR':>8}{'totR':>8}{'PF':>6}{'maxDD':>7}{'hours':>7}"
    lines = [head]
    for name, s in rows:
        if not s.get("n"):
            lines.append(f"{name:<16}{0:>5}")
            continue
        lines.append(f"{name:<16}{s['n']:>5}{s['win%']:>7}{s['avgR']:>8}{s['totR']:>8}{s['PF']:>6}{s['maxDD_R']:>7}{s['avg_hours']:>7}")
    return "\n".join(lines)


def build_report(trades: list[dict], sweep: list[tuple[float, list[dict]]], days: int, fee_bps: float) -> str:
    out = [f"# Future Admiral backtest ({days} days, {fee_bps:g} bps/side costs)\n", "```"]
    out.append(fmt_table([("ALL", stats(trades))]))
    out.append("")
    out.append("By coin\n" + fmt_table([(k, stats([t for t in trades if t["ticker"] == k])) for k in sorted({t["ticker"] for t in trades})]))
    out.append("")
    out.append("By side / setup / grade\n" + fmt_table(
        [(f"{s}", stats([t for t in trades if t["action"] == s])) for s in ("BUY", "SELL")]
        + [(f"{s}", stats([t for t in trades if t["setup"] == s])) for s in ("pullback", "breakout", "momentum", "whale_reversal", "smc_sweep")]
        + [(f"grade {g}", stats([t for t in trades if t["grade"] == g])) for g in ("A", "B")]))
    out.append("")
    out.append("Minimum-score sweep (re-simulated with portfolio rules)\n" + fmt_table([(f"score>={s:g}", stats(tr)) for s, tr in sweep]))
    out.append("```\n")
    out.append("Read it like this: avgR > 0 after costs, profit factor > 1.3 and a sensible max drawdown on at least 100 trades "
               "is the minimum before trusting a rule set. Fewer than ~50 trades means the result is mostly luck.\n")
    return "\n".join(out)


# ------------------------------------------------------------------ grid
VARIANTS = [
    ("V v3 1H Institutional (all 5)", {"min_score": 60, "setup_tf": 1, "setups": "pullback,breakout,momentum,whale_reversal,smc_sweep", "adx_min": 16}),
    ("A base 1H (v2.0)", {"min_score": 62, "setup_tf": 1, "setups": "pullback,breakout", "adx_min": 20}),
    ("B 1H strict (score75, ADX25)", {"min_score": 75, "adx_min": 25, "setup_tf": 1, "setups": "pullback,breakout"}),
    ("C 1H wide stop, TP1.5/3/4.5", {"min_score": 65, "setup_tf": 1, "setups": "pullback,breakout", "stop_min_atr1h": 3.5, "stop_min_atr4h": 1.2, "stop_max_atr4h": 3.5,
                                     "tp1_r": 1.5, "tp2_r": 3.0, "tp3_r": 4.5}),
    ("D 1H pullback only, wide", {"min_score": 65, "setup_tf": 1, "setups": "pullback", "stop_min_atr1h": 3.5, "stop_min_atr4h": 1.2,
                                  "stop_max_atr4h": 3.5, "tp1_r": 1.5, "tp2_r": 3.0, "tp3_r": 4.5}),
    ("E 4H timing default", {"setup_tf": 4, "min_score": 62, "max_hold_h": 120}),
    ("F 4H pullback, TP1.5/3/4.5", {"setup_tf": 4, "min_score": 62, "setups": "pullback", "tp1_r": 1.5, "tp2_r": 3.0,
                                    "tp3_r": 4.5, "max_hold_h": 120}),
    ("G 4H breakout only", {"setup_tf": 4, "min_score": 62, "setups": "breakout", "max_hold_h": 120}),
    ("H 4H ADX25 pullback", {"setup_tf": 4, "min_score": 62, "adx_min": 25, "setups": "pullback", "max_hold_h": 120}),
    ("I 4H score70 pullback", {"setup_tf": 4, "min_score": 70, "setups": "pullback", "max_hold_h": 120}),
    ("J 4H TP1.5/2.5/4, ADX25", {"setup_tf": 4, "min_score": 62, "adx_min": 25, "tp1_r": 1.5, "tp2_r": 2.5, "tp3_r": 4.0,
                                 "max_hold_h": 120}),
    ("K 4H wide stop 1.8-3.5 ATR", {"setup_tf": 4, "min_score": 62, "stop4_min_atr": 1.8, "stop4_max_atr": 3.5,
                                    "tp1_r": 1.2, "tp2_r": 2.2, "tp3_r": 3.5, "max_hold_h": 120}),
]


def _line(name, tr):
    s = stats(tr)
    return s if s.get("n") else {"n": 0}


def run_grid(coins, fee_bps: float, only: str = "") -> str:
    firsts = [c.f1.ts[1500] for c in coins.values() if len(c.f1) > 1500]
    last = max(c.f1.ts[-1] for c in coins.values())
    split = firsts and (min(firsts) + 0.6 * (last - min(firsts)))
    rows = []
    pick = [x.strip().upper() for x in only.split(",") if x.strip()]
    for name, ov in VARIANTS:
        if pick and name[0].upper() not in pick:
            continue
        ov = dict(ov)
        min_score = ov.pop("min_score", 62)
        hold = ov.pop("max_hold_h", 72)
        cfg = {**se.default_config(), "min_score": 0.0, **ov}
        for c in coins.values():
            c.decisions.clear()
        tr = simulate(coins, cfg, min_score=min_score, fee_bps=fee_bps, max_hold_h=hold)
        a, b, full = [t for t in tr if t["entry_ts"] < split], [t for t in tr if t["entry_ts"] >= split], stats(tr)
        sa, sb = stats(a), stats(b)
        ok = (full.get("n", 0) >= 40 and sa.get("n", 0) >= 15 and sb.get("n", 0) >= 15 and sa.get("avgR", -1) > 0
              and sb.get("avgR", -1) > 0 and full.get("PF", 0) >= 1.2)
        rows.append((name, full, sa, sb, ok))
    def cell(s):
        return f"{s['n']:>4} {s['avgR']:>7} {s['PF']:>5}" if s.get("n") else f"{0:>4} {'-':>7} {'-':>5}"
    out = ["# Future Admiral GRID backtest (60% in-sample / 40% out-of-sample)\n", "```",
           f"{'variant':<30}{'FULL n':>5} {'avgR':>7} {'PF':>5} | {'IS n':>4} {'avgR':>7} {'PF':>5} | {'OOS n':>4} {'avgR':>7} {'PF':>5} | pass"]
    for name, full, sa, sb, ok in rows:
        out.append(f"{name:<30}{cell(full)} | {cell(sa)} | {cell(sb)} | {'PASS' if ok else '-'}")
    out.append("```\nPASS = n>=40, avgR>0 in BOTH halves, profit factor >= 1.2 after costs. If nothing passes: do NOT trade these rules.\n")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    ap.add_argument("--fee-bps", type=float, default=8.0, help="cost per side in bps (fee + slippage)")
    ap.add_argument("--min-score", type=float, default=float(os.getenv("ADMIRAL_MIN_SCORE", "58")))
    ap.add_argument("--out", default="backtest_report.md")
    ap.add_argument("--only", default="", help="grid: run only variants whose name starts with these letters, e.g. V,H,I")
    ap.add_argument("--grid", action="store_true", help="compare rule variants with in/out-of-sample split")
    args = ap.parse_args(argv)

    coins: dict[str, CoinData] = {}
    for base in [c.strip().upper() for c in args.coins.split(",") if c.strip()]:
        ticker = f"{base}-USDT"
        t0 = time.time()
        try:
            coins[ticker] = prepare(ticker, fetch_history(ticker, args.days))
            print(f"[+] {ticker}: {len(coins[ticker].f1)} 1H bars in {time.time() - t0:.0f}s")
        except Exception as exc:
            print(f"[!] {ticker}: skipped ({exc})")
    if not coins:
        raise SystemExit("no data downloaded")
    if args.grid:
        report = run_grid(coins, args.fee_bps, args.only)
        print(report)
        with open("grid_report.md", "w", encoding="utf-8") as fh:
            fh.write(report)
        return
    cfg = {**se.default_config(), "min_score": 0.0}
    base_trades = simulate(coins, cfg, min_score=args.min_score, fee_bps=args.fee_bps)
    sweep = [(s, simulate(coins, cfg, min_score=s, fee_bps=args.fee_bps)) for s in (50, 55, 58, 62, 65, 70, 75)]
    report = build_report(base_trades, sweep, args.days, args.fee_bps)
    print(report)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    import csv
    if base_trades:
        with open("backtest_trades.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(base_trades[0].keys()))
            w.writeheader()
            w.writerows(base_trades)


if __name__ == "__main__":
    main()
