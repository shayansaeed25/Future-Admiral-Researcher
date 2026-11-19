"""Google Sheet layout for Future Admiral: exactly THREE tabs (Layout v7 — Institutional Side-by-Side Dashboard).

  Dashboard     - Full-width banners, bordered side-by-side KPI + Coin Breakdown + Outcome Stage + Settings tables,
                  4 charts, and bordered "Market now" table (with helper columns AA..BJ auto-hidden)
  Positions     - Every signal / trade (clean white/zebra rows + high-contrast status pill badges)
  System Health - Data feed & system health diagnostics

Everything else is deleted. Applied automatically once per layout version / coin list.
Set ADMIRAL_RESET_SHEET=1 (workflow input "reset_sheet") to also wipe the ledgers for a fresh start.
Nothing here can block trade logging: each step is wrapped and a failure prints Google's error text.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

LAYOUT_VERSION = "7"
NAVY = "1F2A44"
SLATE = "334155"
BANNER_BG = "F1F5F9"
ZEBRA_BG = "F8FAFC"
BORDER_HEX = "CBD5E1"
TABS_ORDER = ["Dashboard", "Positions", "System Health"]
MAX_ROWS = 2000
MARKET_ROW = 69            # 1-based row where the "Market now" block starts (write_board uses it)
MARKER_CELL = "BJ1"

N = f"Positions!$N$2:$N${MAX_ROWS}"
AF = f"Positions!$AF$2:$AF${MAX_ROWS}"
D = f"Positions!$D$2:$D${MAX_ROWS}"
E = f"Positions!$E$2:$E${MAX_ROWS}"
R = f"Positions!$R$2:$R${MAX_ROWS}"
CLOSED_STATUSES = ("TP3_HIT", "SL_HIT", "BE_EXIT", "TIME_EXIT")
OPEN_STATUSES = ("PENDING", "TP1_HIT", "TP2_HIT")
CLOSED = "(" + "+".join(f'({N}="{s}")' for s in CLOSED_STATUSES) + f")*ISNUMBER({AF})"
# Settings cells on the Dashboard
NOTIONAL, FEE, SLIP, MIN_TRADES, PF_GATE, COST = "$B$28", "$B$29", "$B$30", "$B$31", "$B$32", "$B$33"


def _sum_closed(extra: str = "") -> str:
    return "+".join(f'SUMIFS({AF},{N},"{s}"{extra})' for s in CLOSED_STATUSES)


def _count_closed(extra: str = "") -> str:
    return "+".join(f'COUNTIFS({N},"{s}",{AF},"<>"{extra})' for s in CLOSED_STATUSES)


def rgb(hexcode: str) -> dict[str, float]:
    return {"red": int(hexcode[0:2], 16) / 255, "green": int(hexcode[2:4], 16) / 255, "blue": int(hexcode[4:6], 16) / 255}


def col_letter(idx: int) -> str:
    """0-based column index -> A1 letters."""
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


# helper-data blocks (to the right of the dashboard, auto-hidden columns AA..BG, header row 4)
EQ0, BC0, DY0, OC0, SD0 = 26, 37, 47, 53, 56      # first column index of each block (AA, AL, AV, BB, BE)
EQ = {k: col_letter(EQ0 + i) for i, k in enumerate(["n", "closed", "coin", "side", "outcome", "gross", "net", "cum", "peak", "dd"])}


# ---- Dashboard contents --------------------------------------------------------------------------

def dashboard_values() -> list[list[Any]]:
    max_closed = ",".join(f'MAXIFS({AF},{N},"{s}")' for s in CLOSED_STATUSES)
    min_closed = ",".join(f'MINIFS({AF},{N},"{s}")' for s in CLOSED_STATUSES)
    rows = [
        ["Future Admiral — Institutional Research & Paper Trading Dashboard"],
        ["1H Institutional Setup Engine + Per-Run Rolling Strategy Backtester + 30m Global Macro/ETF Radar · ($100 Fixed Notional per Trade)"],
        [],
        ["Metric", "Value", "How to read it"],
        ["Closed trades (n)", f"=SUMPRODUCT({CLOSED})", "Sample size. Under ~40 trades the numbers are mostly luck."],
        ["Open trades now", "=" + "+".join(f'COUNTIF({N},"{s}")' for s in OPEN_STATUSES), "Trades still running (SL/TP watched every hour)."],
        ["Winners (gross > 0)", f"=SUMPRODUCT({CLOSED}*({AF}>0))", "Closed with a profit before costs."],
        ["Losers", "=B5-B7", "Closed flat or at a loss."],
        ["Win rate", "=IF(B5=0,0,B7/B5)", "Trend systems often win 35-50%; size of wins matters more."],
        ["Gross profit ($)", "=" + _sum_closed(f',{AF},">0"'), "Sum of winning trades before costs."],
        ["Gross loss ($)", "=" + _sum_closed(f',{AF},"<0"'), "Sum of losing trades before costs."],
        ["Gross P&L ($)", "=B10+B11", "Result before any costs."],
        ["Assumed costs ($)", f"=B5*{COST}", "Closed trades x round-trip fee + slippage (Settings below)."],
        ["Net P&L after costs ($)", "=B12-B13", "The number that matters."],
        ["Net return on capital used", f"=IF(B5=0,0,B14/(B5*{NOTIONAL}))", "Net P&L / (size per trade x trades)."],
        ["Average winner ($, gross)", "=IF(B7=0,0,B10/B7)", "Average dollar gain on winning closed trades."],
        ["Average loser ($, gross)", "=IF(B8=0,0,B11/B8)", "Healthy: average winner clearly bigger than average loser."],
        ["Profit factor (gross)", "=IF(B11=0,0,B10/-B11)", "Gross profit / gross loss. Above 1 = profitable before costs."],
        ["Profit factor (net, approx.)", f"=IF((-B11+{COST}*B8)=0,0,(B10-{COST}*B7)/(-B11+{COST}*B8))", "After assumed costs. Needs to clear the gate in Settings."],
        ["Expectancy per trade ($, net)", "=IF(B5=0,0,B14/B5)", "Average result of one trade after costs. Must be positive."],
        ["Best closed trade ($)", f"=IF(B5=0,0,MAX({max_closed}))", "Largest single-trade gross gain recorded in Positions."],
        ["Worst closed trade ($)", f"=IF(B5=0,0,MIN({min_closed}))", "Largest single-trade gross loss recorded in Positions."],
        ["Max drawdown ($, net)", f"=MIN(0,${EQ['dd']}$5:${EQ['dd']}$304)", "Biggest fall from a previous equity peak."],
        ["Verdict", f'=IF(B5<{MIN_TRADES},"TESTING - only "&B5&" closed trades (need "&{MIN_TRADES}&")",'
                    f'IF(B19>={PF_GATE},"YES - edge visible after costs",IF(B14>0,"YES - profitable after costs (+$"&TEXT(B14,"0.00")&")","NO - not good enough after costs")))',
         "Enough trades AND positive net profit / profit factor above gate."],
        ["Health alerts needing action", "=COUNTIF('System Health'!$E$2:$E$5000,\"ACTION\")", "0 is good. Details on the System Health tab."],
        [],
        ["Settings (edit the yellow cells)", "Value", "Why"],
        ["Paper size per trade (USD)", 100, "Every paper trade uses this notional."],
        ["Fee per side (% of notional)", 0.0005, "Assumed taker fee (OKX perpetuals ~0.05%). Use your real fee."],
        ["Slippage per side (% of notional)", 0.0003, "Cost of not getting the exact price on liquid coins."],
        ["Minimum closed trades to judge", 40, "Below this the verdict stays TESTING."],
        ["Minimum net profit factor", 1.2, "Gate for the verdict."],
        ["Round-trip cost per trade ($)", "=B28*2*(B29+B30)", "Formula: size x 2 x (fee + slippage)."],
    ]
    return rows


def visible_right_tables(coins: list[str]) -> list[list[Any]]:
    """Build the visible side-by-side Coin Performance table (E4:I25) and Outcome Stage table (E27:I33) on Dashboard."""
    rows: list[list[Any]] = [
        [],
        [],
        [],
        ["Coin Pair", "Closed Trades", "Winners", "Win Rate", "Total Gross P&L ($)"],
    ]
    top_coins = list(coins)[:20]
    for idx, coin in enumerate(top_coins, start=5):
        rows.append([
            coin,
            f"=SUMPRODUCT(({D}=$E{idx})*{CLOSED})",
            f"=SUMPRODUCT(({D}=$E{idx})*{CLOSED}*({AF}>0))",
            f"=IF(F{idx}=0,0,G{idx}/F{idx})",
            f"=SUMIFS({AF},{D},$E{idx})",
        ])
    while len(rows) < 24:
        rows.append(["", "", "", "", ""])
    rows.append([
        "Total (All Coins)",
        "=SUM(F5:F24)",
        "=SUM(G5:G24)",
        "=IF(F25=0,0,G25/F25)",
        "=SUM(I5:I24)",
    ])
    rows.append([])  # Row 26 spacer
    rows.append(["Outcome Stage", "Status Code", "Trade Count", "Share of Trades", "Gross P&L ($)"])  # Row 27
    outcomes = [
        ("Full Target Reached (+3R)", "TP3_HIT", f'=COUNTIF({N},"TP3_HIT")', f'=SUMIFS({AF},{N},"TP3_HIT")'),
        ("Breakeven Protected (+1R/+2R Banked)", "BE_EXIT", f'=COUNTIF({N},"BE_EXIT")', f'=SUMIFS({AF},{N},"BE_EXIT")'),
        ("72h Time Limit Exit", "TIME_EXIT", f'=COUNTIF({N},"TIME_EXIT")', f'=SUMIFS({AF},{N},"TIME_EXIT")'),
        ("Stop-Loss Hit (-1R)", "SL_HIT", f'=COUNTIF({N},"SL_HIT")', f'=SUMIFS({AF},{N},"SL_HIT")'),
        ("Active Open Positions (Live)", "OPEN", "=" + "+".join(f'COUNTIF({N},"{s}")' for s in OPEN_STATUSES),
         "=" + "+".join(f'SUMIFS({AF},{N},"{s}")' for s in OPEN_STATUSES)),
    ]
    for offset, (label, code, cnt_f, pnl_f) in enumerate(outcomes, start=28):
        rows.append([
            label,
            code,
            cnt_f,
            f"=IF(SUM($G$28:$G$32)=0,0,G{offset}/SUM($G$28:$G$32))",
            pnl_f,
        ])
    rows.append([
        "Total Portfolio (Closed + Open)",
        "ALL",
        "=SUM(G28:G32)",
        "=SUM(H28:H32)",
        "=SUM(I28:I32)",
    ])
    return rows


def equity_block() -> list[list[Any]]:
    rows = [["Chart data - equity (automatic, do not edit)"], [], [],
            ["#", "Closed (PKT)", "Coin", "Side", "Outcome", "Gross P&L ($)", "Net P&L ($)", "Cumulative net ($)", "Peak ($)", "Drawdown ($)"]]
    cl = "+".join(f'({N}="{s}")' for s in CLOSED_STATUSES)
    flt = (f'=IFERROR(FILTER({{Positions!$R$2:$R${MAX_ROWS},Positions!$D$2:$D${MAX_ROWS},Positions!$E$2:$E${MAX_ROWS},'
           f'Positions!$N$2:$N${MAX_ROWS},Positions!$AF$2:$AF${MAX_ROWS}}},ISNUMBER({AF}),({cl})>0),"")')
    c = EQ
    for i in range(5, 305):
        rows.append([f'=IF(${c["closed"]}{i}="","",ROW()-4)', flt if i == 5 else None, None, None, None, None,
                     f'=IF(${c["n"]}{i}="","",{c["gross"]}{i}-{COST})', f'=IF(${c["n"]}{i}="","",SUM({c["net"]}$5:{c["net"]}{i}))',
                     f'=IF(${c["n"]}{i}="","",MAX(0,MAX({c["cum"]}$5:{c["cum"]}{i})))', f'=IF(${c["n"]}{i}="","",{c["cum"]}{i}-{c["peak"]}{i})'])
    return rows


def by_coin_block(coins: list[str]) -> list[list[Any]]:
    rows = [["Chart data - by coin"], [], [], ["Coin", "Closed", "Wins", "Losses", "Win rate", "Gross P&L ($)", "Net P&L ($)", "Avg net / trade ($)", "Verdict"]]
    L0 = col_letter(BC0)
    for i, coin in enumerate(coins, 5):
        a = f"${L0}{i}"
        b, c_, d_, f_, g_ = (col_letter(BC0 + k) for k in (1, 2, 3, 5, 6))
        rows.append([coin, f"=SUMPRODUCT(({D}={a})*{CLOSED})", f"=SUMPRODUCT(({D}={a})*{CLOSED}*({AF}>0))", f"={b}{i}-{c_}{i}",
                     f"=IF({b}{i}=0,0,{c_}{i}/{b}{i})", "=" + _sum_closed(f',{D},{a}'), f"={f_}{i}-{b}{i}*{COST}",
                     f"=IF({b}{i}=0,0,{g_}{i}/{b}{i})", f'=IF({b}{i}=0,"-",IF({b}{i}<{MIN_TRADES},"TESTING",IF({g_}{i}>0,"PROMOTE","CUT")))'])
    return rows


def daily_block() -> list[list[Any]]:
    rows = [["Chart data - last 31 days"], [], [], ["Date", "Closed trades", "Gross P&L ($)", "Net P&L ($)", "Cumulative net ($)"]]
    A, B, C, Dn, Em = (col_letter(DY0 + k) for k in range(5))
    for i in range(5, 36):
        rows.append([f'=TEXT(TODAY()-{35 - i},"yyyy-mm-dd")', f'=COUNTIFS({R},${A}{i}&"*",{AF},"<>")', f'=SUMIFS({AF},{R},${A}{i}&"*")',
                     f"={C}{i}-{B}{i}*{COST}", f"=SUM({Dn}$5:{Dn}{i})"])
    return rows


def outcome_block() -> list[list[Any]]:
    return [["Chart data - outcomes"], [], [], ["Outcome", "Trades"],
            ["Reached TP3", f'=COUNTIFS({N},"TP3_HIT",{AF},"<>")'], ["Breakeven exit", f'=COUNTIFS({N},"BE_EXIT",{AF},"<>")'],
            ["Stop loss", f'=COUNTIFS({N},"SL_HIT",{AF},"<>")'], ["Time exit", f'=COUNTIFS({N},"TIME_EXIT",{AF},"<>")']]


def side_block() -> list[list[Any]]:
    return [["Chart data - BUY vs SELL"], [], [], ["Side", "Gross P&L ($)", "Closed trades"],
            ["BUY", "=" + _sum_closed(f',{E},"BUY"'), "=" + _count_closed(f',{E},"BUY"')],
            ["SELL", "=" + _sum_closed(f',{E},"SELL"'), "=" + _count_closed(f',{E},"SELL"')]]


# ---- Google Sheets API plumbing -----------------------------------------------------------------

def _gs():
    import google_sheets_sync as gs
    return gs


def _safe(label: str, fn) -> bool:
    try:
        fn()
        return True
    except Exception as exc:
        detail = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = " | " + resp.text[:400]
            except Exception:
                pass
        print(f"[!] Sheet layout step '{label}' failed: {type(exc).__name__}: {str(exc)[:120]}{detail}")
        return False


def _put(tab: str, values: list[list[Any]], mode: str = "USER_ENTERED", anchor: str = "A1") -> None:
    gs = _gs()
    target = quote(f"'{tab}'!{anchor}", safe="!'")
    gs._request("PUT", f"values/{target}?valueInputOption={mode}", json={"values": [list(r) for r in values]})


def _rng(sheet: int, r0=0, r1=None, c0=0, c1=None) -> dict:
    out = {"sheetId": sheet, "startRowIndex": r0, "startColumnIndex": c0}
    if r1 is not None:
        out["endRowIndex"] = r1
    if c1 is not None:
        out["endColumnIndex"] = c1
    return out


def _fmt(sheet, r0, r1, c0, c1, fmt: dict, fields: str) -> dict:
    return {"repeatCell": {"range": _rng(sheet, r0, r1, c0, c1), "cell": {"userEnteredFormat": fmt}, "fields": f"userEnteredFormat({fields})"}}


def _borders(sheet, r0, r1, c0, c1, color_hex: str = BORDER_HEX, style: str = "SOLID") -> dict:
    side = {"style": style, "color": rgb(color_hex)}
    return {"updateBorders": {"range": _rng(sheet, r0, r1, c0, c1), "top": side, "bottom": side, "left": side, "right": side,
                              "innerHorizontal": side, "innerVertical": side}}


def _width(sheet, widths: list[int]) -> list[dict]:
    return [{"updateDimensionProperties": {"range": {"sheetId": sheet, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                           "properties": {"pixelSize": w}, "fields": "pixelSize"}} for i, w in enumerate(widths)]


def _header_style(sheet, row, c1, c0=0, bg_hex=NAVY) -> list[dict]:
    return [_fmt(sheet, row, row + 1, c0, c1, {"backgroundColor": rgb(bg_hex), "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                                               "wrapStrategy": "WRAP", "textFormat": {"foregroundColor": rgb("FFFFFF"), "bold": True, "fontFamily": "Arial", "fontSize": 10}},
                 "backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat"),
            {"updateDimensionProperties": {"range": {"sheetId": sheet, "dimension": "ROWS", "startIndex": row, "endIndex": row + 1},
                                           "properties": {"pixelSize": 32}, "fields": "pixelSize"}}]


def _title_style(sheet) -> list[dict]:
    return [
        {"mergeCells": {"range": _rng(sheet, 0, 1, 0, 9), "mergeType": "MERGE_ALL"}},
        {"mergeCells": {"range": _rng(sheet, 1, 2, 0, 9), "mergeType": "MERGE_ALL"}},
        _fmt(sheet, 0, 1, 0, 9, {"backgroundColor": rgb(NAVY), "verticalAlignment": "MIDDLE",
                                 "textFormat": {"bold": True, "fontSize": 14, "fontFamily": "Arial", "foregroundColor": rgb("FFFFFF")}},
             "backgroundColor,verticalAlignment,textFormat"),
        _fmt(sheet, 1, 2, 0, 9, {"backgroundColor": rgb(BANNER_BG), "verticalAlignment": "MIDDLE",
                                 "textFormat": {"italic": True, "fontSize": 10, "fontFamily": "Arial", "foregroundColor": rgb(SLATE)}},
             "backgroundColor,verticalAlignment,textFormat"),
        {"updateDimensionProperties": {"range": {"sheetId": sheet, "dimension": "ROWS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 38}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}, "properties": {"pixelSize": 26}, "fields": "pixelSize"}},
    ]


def _base_font(sheet, r0=1) -> dict:
    return _fmt(sheet, r0, None, 0, None, {
        "backgroundColor": rgb("FFFFFF"),
        "textFormat": {"fontFamily": "Arial", "fontSize": 10, "foregroundColor": rgb("1E293B"), "bold": False},
        "verticalAlignment": "MIDDLE",
    }, "backgroundColor,textFormat,verticalAlignment")


def _cf_text(sheet, c0, c1, r0, text, bg, fg, kind="TEXT_EQ", bold=True, r1=None) -> dict:
    return {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [_rng(sheet, r0, r1, c0, c1)], "booleanRule": {
        "condition": {"type": kind, "values": [{"userEnteredValue": text}]},
        "format": {"backgroundColor": rgb(bg), "textFormat": {"foregroundColor": rgb(fg), "bold": bold}}}}}}


def _cf_formula(sheet, c0, c1, r0, formula, bg, fg=None, bold=False, r1=None) -> dict:
    fmt = {"backgroundColor": rgb(bg)} if bg else {}
    if fg:
        fmt["textFormat"] = {"foregroundColor": rgb(fg), "bold": bold}
    return {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [_rng(sheet, r0, r1, c0, c1)], "booleanRule": {
        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": fmt}}}}


# colour pairs (background, text)
GREEN, RED, AMBER, BLUEC, GREY = ("DCFCE7", "166534"), ("FEE2E2", "991B1B"), ("FEF3C7", "92400E"), ("E0F2FE", "075985"), ("F1F5F9", "475569")
ROW_TINT = {"green": "F4FBF7", "red": "FEF7F7", "amber": "FFFDF5", "blue": "F8FAFC"}


def _status_rules(sheet, col, r0=1) -> list[dict]:
    pairs = [("PENDING", BLUEC), ("TP1_HIT", GREEN), ("TP2_HIT", GREEN), ("TP3_HIT", GREEN), ("SL_HIT", RED), ("BE_EXIT", AMBER), ("TIME_EXIT", GREY)]
    return [_cf_text(sheet, col, col + 1, r0, v, c[0], c[1]) for v, c in pairs]


# ---- formatting requests -----------------------------------------------------------------------

def _dash_requests(s: int, n: int) -> list[dict]:
    money = {"type": "NUMBER", "pattern": "$#,##0.00;[Red]-$#,##0.00"}
    req = [_base_font(s, 0)] + _title_style(s)
    # Column widths for Columns A..I (Left table A:C, Spacer D, Right tables E:I)
    req += _width(s, [235, 165, 360, 26, 175, 105, 95, 105, 150])
    # Show gridlines and freeze top 3 rows
    req += [{"updateSheetProperties": {"properties": {"sheetId": s, "gridProperties": {"hideGridlines": False, "frozenRowCount": 3}},
                                       "fields": "gridProperties(hideGridlines,frozenRowCount)"}}]

    # Left Table 1 (A4:C25): Portfolio KPIs
    req += _header_style(s, 3, 3, c0=0, bg_hex=NAVY)
    for r_z in range(4, 25, 2):
        req.append(_fmt(s, r_z, r_z + 1, 0, 3, {"backgroundColor": rgb(ZEBRA_BG)}, "backgroundColor"))
    req.append(_borders(s, 3, 25, 0, 3))
    req += [_fmt(s, 4, 25, 0, 1, {"textFormat": {"fontFamily": "Arial", "fontSize": 10, "foregroundColor": rgb("0F172A")}}, "textFormat"),
            _fmt(s, 11, 14, 0, 1, {"textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10, "foregroundColor": rgb("0F172A")}}, "textFormat"),
            _fmt(s, 4, 25, 1, 2, {"horizontalAlignment": "RIGHT", "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "horizontalAlignment,textFormat"),
            _fmt(s, 4, 25, 2, 3, {"wrapStrategy": "WRAP", "textFormat": {"foregroundColor": rgb("475569"), "fontFamily": "Arial", "fontSize": 9}}, "wrapStrategy,textFormat"),
            _fmt(s, 4, 8, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "0"}}, "numberFormat"),
            _fmt(s, 8, 9, 1, 2, {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}, "numberFormat"),
            _fmt(s, 9, 14, 1, 2, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 14, 15, 1, 2, {"numberFormat": {"type": "PERCENT", "pattern": "0.00%;[Red]-0.00%"}}, "numberFormat"),
            _fmt(s, 15, 17, 1, 2, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 17, 19, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}}, "numberFormat"),
            _fmt(s, 19, 23, 1, 2, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 23, 24, 1, 2, {"wrapStrategy": "WRAP", "horizontalAlignment": "CENTER"}, "wrapStrategy,horizontalAlignment"),
            _fmt(s, 24, 25, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "0"}}, "numberFormat")]

    # Left Table 2 (A27:C33): Settings
    req += _header_style(s, 26, 3, c0=0, bg_hex=SLATE)
    req.append(_borders(s, 26, 33, 0, 3))
    req += [_fmt(s, 27, 33, 1, 2, {"horizontalAlignment": "RIGHT"}, "horizontalAlignment"),
            _fmt(s, 27, 32, 1, 2, {"backgroundColor": rgb("FFFBEB"), "textFormat": {"foregroundColor": rgb("1D4ED8"), "bold": True, "fontFamily": "Arial", "fontSize": 10}}, "backgroundColor,textFormat"),
            _fmt(s, 27, 28, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "$#,##0.00"}}, "numberFormat"),
            _fmt(s, 28, 30, 1, 2, {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}, "numberFormat"),
            _fmt(s, 30, 31, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "0"}}, "numberFormat"),
            _fmt(s, 31, 32, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}}, "numberFormat"),
            _fmt(s, 32, 33, 1, 2, {"backgroundColor": rgb(BANNER_BG), "numberFormat": {"type": "NUMBER", "pattern": "$#,##0.00"}, "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "backgroundColor,numberFormat,textFormat"),
            _fmt(s, 27, 33, 2, 3, {"wrapStrategy": "WRAP", "textFormat": {"foregroundColor": rgb("475569"), "fontFamily": "Arial", "fontSize": 9}}, "wrapStrategy,textFormat")]
    req += [_cf_formula(s, 1, 2, 23, '=LEFT($B24,3)="YES"', GREEN[0], GREEN[1], True, r1=24),
            _cf_formula(s, 1, 2, 23, '=LEFT($B24,2)="NO"', RED[0], RED[1], True, r1=24),
            _cf_formula(s, 1, 2, 23, '=LEFT($B24,7)="TESTING"', AMBER[0], AMBER[1], True, r1=24),
            _cf_formula(s, 1, 2, 24, '=$B25>0', RED[0], RED[1], True, r1=25)]

    # Right Table 1 (E4:I25): Coin Pair Performance Breakdown
    req += _header_style(s, 3, 9, c0=4, bg_hex=NAVY)
    for r_z in range(4, 24, 2):
        req.append(_fmt(s, r_z, r_z + 1, 4, 9, {"backgroundColor": rgb(ZEBRA_BG)}, "backgroundColor"))
    req.append(_fmt(s, 24, 25, 4, 9, {"backgroundColor": rgb(BANNER_BG), "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "backgroundColor,textFormat"))
    req.append(_borders(s, 3, 25, 4, 9))
    req += [_fmt(s, 4, 25, 4, 5, {"horizontalAlignment": "LEFT", "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "horizontalAlignment,textFormat"),
            _fmt(s, 4, 25, 5, 7, {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "NUMBER", "pattern": "0"}}, "horizontalAlignment,numberFormat"),
            _fmt(s, 4, 25, 7, 8, {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}, "horizontalAlignment,numberFormat"),
            _fmt(s, 4, 25, 8, 9, {"horizontalAlignment": "RIGHT", "numberFormat": money, "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "horizontalAlignment,numberFormat,textFormat")]

    # Right Table 2 (E27:I33): Outcome Stage Breakdown
    req += _header_style(s, 26, 9, c0=4, bg_hex=SLATE)
    req.append(_fmt(s, 32, 33, 4, 9, {"backgroundColor": rgb(BANNER_BG), "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "backgroundColor,textFormat"))
    req.append(_borders(s, 26, 33, 4, 9))
    req += [_fmt(s, 27, 33, 4, 5, {"horizontalAlignment": "LEFT"}, "horizontalAlignment"),
            _fmt(s, 27, 33, 5, 6, {"horizontalAlignment": "CENTER", "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 9}}, "horizontalAlignment,textFormat"),
            _fmt(s, 27, 33, 6, 7, {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "NUMBER", "pattern": "0"}, "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "horizontalAlignment,numberFormat,textFormat"),
            _fmt(s, 27, 33, 7, 8, {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}, "horizontalAlignment,numberFormat"),
            _fmt(s, 27, 33, 8, 9, {"horizontalAlignment": "RIGHT", "numberFormat": money, "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "horizontalAlignment,numberFormat,textFormat")]
    for code, col_pair in (("TP3_HIT", GREEN), ("BE_EXIT", AMBER), ("TIME_EXIT", GREY), ("SL_HIT", RED), ("OPEN", BLUEC)):
        req.append(_cf_text(s, 5, 6, 27, code, col_pair[0], col_pair[1], r1=33))

    # Market now block (Row 69..94)
    m = MARKET_ROW - 1
    req += [{"mergeCells": {"range": _rng(s, m, m + 1, 0, 7), "mergeType": "MERGE_ALL"}},
            {"mergeCells": {"range": _rng(s, m, m + 1, 7, 9), "mergeType": "MERGE_ALL"}},
            _fmt(s, m, m + 1, 0, 7, {"backgroundColor": rgb(NAVY), "verticalAlignment": "MIDDLE",
                                     "textFormat": {"bold": True, "fontSize": 12, "fontFamily": "Arial", "foregroundColor": rgb("FFFFFF")}},
                 "backgroundColor,verticalAlignment,textFormat"),
            _fmt(s, m, m + 1, 7, 9, {"backgroundColor": rgb(NAVY), "horizontalAlignment": "RIGHT", "verticalAlignment": "MIDDLE",
                                     "textFormat": {"bold": True, "fontSize": 10, "fontFamily": "Arial", "foregroundColor": rgb("FFFFFF")}},
                 "backgroundColor,horizontalAlignment,verticalAlignment,textFormat"),
            {"updateDimensionProperties": {"range": {"sheetId": s, "dimension": "ROWS", "startIndex": m, "endIndex": m + 1}, "properties": {"pixelSize": 34}, "fields": "pixelSize"}},
            _fmt(s, m + 1, m + 3, 0, 1, {"backgroundColor": rgb(BANNER_BG), "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}, "verticalAlignment": "MIDDLE"}, "backgroundColor,verticalAlignment,textFormat"),
            _fmt(s, m + 1, m + 2, 1, 9, {"backgroundColor": rgb("FFF8E1"), "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE", "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 9, "foregroundColor": rgb("7F5200")}}, "backgroundColor,wrapStrategy,verticalAlignment,textFormat"),
            _fmt(s, m + 2, m + 3, 1, 9, {"backgroundColor": rgb(BANNER_BG), "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE", "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 9, "foregroundColor": rgb("334155")}}, "backgroundColor,wrapStrategy,verticalAlignment,textFormat"),
            {"mergeCells": {"range": _rng(s, m + 1, m + 2, 1, 9), "mergeType": "MERGE_ALL"}},
            {"mergeCells": {"range": _rng(s, m + 2, m + 3, 1, 9), "mergeType": "MERGE_ALL"}},
            _borders(s, m + 1, m + 3, 0, 9),
            {"updateDimensionProperties": {"range": {"sheetId": s, "dimension": "ROWS", "startIndex": m + 1, "endIndex": m + 3}, "properties": {"pixelSize": 38}, "fields": "pixelSize"}}]
    req += _header_style(s, m + 4, 9, c0=0, bg_hex=NAVY)
    r0 = m + 5
    for r_z in range(r0, r0 + 20, 2):
        req.append(_fmt(s, r_z, r_z + 1, 0, 9, {"backgroundColor": rgb(ZEBRA_BG)}, "backgroundColor"))
    req.append(_borders(s, m + 4, r0 + 20, 0, 9))
    req += [_fmt(s, r0, r0 + 25, 0, 1, {"textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "textFormat"),
            _fmt(s, r0, r0 + 25, 1, 2, {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00####"}, "horizontalAlignment": "RIGHT"}, "numberFormat,horizontalAlignment"),
            _fmt(s, r0, r0 + 25, 2, 3, {"wrapStrategy": "WRAP", "horizontalAlignment": "LEFT"}, "wrapStrategy,horizontalAlignment"),
            _fmt(s, r0, r0 + 25, 3, 6, {"horizontalAlignment": "CENTER", "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 9}}, "horizontalAlignment,textFormat"),
            _fmt(s, r0, r0 + 25, 6, 8, {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "NUMBER", "pattern": "0.0"}}, "horizontalAlignment,numberFormat"),
            _fmt(s, r0, r0 + 25, 8, 9, {"horizontalAlignment": "CENTER", "textFormat": {"fontFamily": "Arial", "fontSize": 9, "foregroundColor": rgb("475569")}}, "horizontalAlignment,textFormat")]
    for text, colors in (("NO DATA", RED), ("SETUP", AMBER), ("TRADE OPEN", BLUEC), ("WAIT", GREY), ("SELL SIGNAL", RED), ("BUY SIGNAL", GREEN)):
        req.append(_cf_text(s, 2, 3, r0, text, colors[0], colors[1], kind="TEXT_CONTAINS", r1=r0 + 25))
    for text, colors in (("BULLISH", GREEN), ("BEARISH", RED), ("MIXED", AMBER)):
        req.append(_cf_text(s, 3, 6, r0, text, colors[0], colors[1], kind="TEXT_EQ", bold=True, r1=r0 + 25))

    # Helper blocks (AA..BJ): format numbers & auto-hide columns AA..BM (indices 26..65) so Dashboard stays 100% clean!
    for c0, ncols in ((EQ0, 10), (BC0, 9), (DY0, 5), (OC0, 2), (SD0, 3)):
        req.append(_fmt(s, 3, 4, c0, c0 + ncols, {"backgroundColor": rgb("D9DEE7"), "textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 9}, "wrapStrategy": "WRAP"}, "backgroundColor,textFormat,wrapStrategy"))
        req.append(_fmt(s, 4, 304, c0, c0 + ncols, {"textFormat": {"foregroundColor": rgb("7A8394"), "fontFamily": "Arial", "fontSize": 9}}, "textFormat"))
        req += _width_range(s, c0, c0 + ncols, 95)
    req += [_fmt(s, 4, 304, EQ0 + 5, EQ0 + 10, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 4, 4 + n + 1, BC0 + 5, BC0 + 8, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 4, 36, DY0 + 2, DY0 + 5, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 4, 6, SD0 + 1, SD0 + 2, {"numberFormat": money}, "numberFormat"),
            _fmt(s, 4, 4 + n, BC0 + 4, BC0 + 5, {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}, "numberFormat"),
            _cf_text(s, BC0 + 8, BC0 + 9, 4, "PROMOTE", GREEN[0], GREEN[1]),
            _cf_text(s, BC0 + 8, BC0 + 9, 4, "CUT", RED[0], RED[1]),
            _cf_text(s, BC0 + 8, BC0 + 9, 4, "TESTING", AMBER[0], AMBER[1]),
            {"updateDimensionProperties": {"range": {"sheetId": s, "dimension": "COLUMNS", "startIndex": 9, "endIndex": 65},
                                           "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}]
    return req


def _width_range(sheet, c0, c1, px) -> list[dict]:
    return [{"updateDimensionProperties": {"range": {"sheetId": sheet, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c1},
                                           "properties": {"pixelSize": px}, "fields": "pixelSize"}}]


def _format_requests(ids: dict[str, int], coins: list[str]) -> dict[str, list[dict]]:
    steps: dict[str, list[dict]] = {"Dashboard": _dash_requests(ids["Dashboard"], len(coins))}
    s = ids["Positions"]
    req = _header_style(s, 0, 34) + [_base_font(s, 1)] + _width(s, [180, 155, 85, 105, 75, 115, 115, 110, 90, 110, 90, 110, 90, 120, 115, 155, 115, 155, 120, 120, 300, 75, 90, 250, 105, 105, 105, 105, 105, 125, 130, 125, 105, 300])
    req += [{"updateSheetProperties": {"properties": {"sheetId": s, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 5, "hideGridlines": False}},
                                       "fields": "gridProperties(frozenRowCount,frozenColumnCount,hideGridlines)"}},
            {"setBasicFilter": {"filter": {"range": _rng(s, 0, None, 0, 34)}}},
            _borders(s, 0, 200, 0, 34)]
    for c in (5, 6, 7, 9, 11, 14, 16, 24, 25):
        req.append(_fmt(s, 1, None, c, c + 1, {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00####"}, "horizontalAlignment": "RIGHT"}, "numberFormat,horizontalAlignment"))
    for c in (26, 29, 30, 31):
        req.append(_fmt(s, 1, None, c, c + 1, {"numberFormat": {"type": "NUMBER", "pattern": "$#,##0.00;[Red]-$#,##0.00;-"}, "horizontalAlignment": "RIGHT"}, "numberFormat,horizontalAlignment"))
    req += [_fmt(s, 1, None, 32, 33, {"numberFormat": {"type": "PERCENT", "pattern": "0.00%;[Red]-0.00%"}, "horizontalAlignment": "RIGHT"}, "numberFormat,horizontalAlignment"),
            _fmt(s, 1, None, 21, 23, {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}, "horizontalAlignment": "CENTER"}, "numberFormat,horizontalAlignment"),
            _fmt(s, 1, None, 3, 4, {"textFormat": {"bold": True, "fontFamily": "Arial", "fontSize": 10}}, "textFormat")]
    for c in (2, 4, 8, 10, 12, 13, 19):
        req.append(_fmt(s, 1, None, c, c + 1, {"horizontalAlignment": "CENTER"}, "horizontalAlignment"))
    for c in (0, 1, 15, 17, 20, 23, 33):
        req.append(_fmt(s, 1, None, c, c + 1, {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}, "wrapStrategy,verticalAlignment"))
    tint_rules = [(f'=$N2="TP3_HIT"', ROW_TINT["green"]), (f'=$N2="TP2_HIT"', ROW_TINT["green"]), (f'=$N2="TP1_HIT"', ROW_TINT["green"]),
                  (f'=$N2="SL_HIT"', ROW_TINT["red"]), (f'=$N2="BE_EXIT"', ROW_TINT["amber"]), (f'=$N2="PENDING"', ROW_TINT["blue"])]
    for formula, tint in reversed(tint_rules):
        req.append(_cf_formula(s, 0, 34, 1, formula, tint))
    for c in (8, 10, 12):
        req += [_cf_text(s, c, c + 1, 1, "HIT", GREEN[0], GREEN[1]), _cf_text(s, c, c + 1, 1, "PENDING", BLUEC[0], BLUEC[1])]
    req += [_cf_text(s, 4, 5, 1, "BUY", GREEN[0], GREEN[1]), _cf_text(s, 4, 5, 1, "SELL", RED[0], RED[1])]
    req += [_cf_formula(s, 29, 33, 1, '=AND(ISNUMBER(AD2),AD2<0)', None, RED[1], True),
            _cf_formula(s, 29, 33, 1, '=AND(ISNUMBER(AD2),AD2>0)', None, GREEN[1], True)]
    req += _status_rules(s, 13, 1)
    steps["Positions"] = req

    s = ids["System Health"]
    req = _header_style(s, 0, 7) + [_base_font(s, 1)] + _width(s, [210, 160, 100, 110, 100, 180, 720])
    req += [{"updateSheetProperties": {"properties": {"sheetId": s, "gridProperties": {"frozenRowCount": 1, "hideGridlines": False}},
                                       "fields": "gridProperties(frozenRowCount,hideGridlines)"}},
            {"setBasicFilter": {"filter": {"range": _rng(s, 0, None, 0, 7)}}},
            _borders(s, 0, 100, 0, 7),
            _fmt(s, 1, None, 4, 5, {"horizontalAlignment": "CENTER"}, "horizontalAlignment"),
            _fmt(s, 1, None, 5, 7, {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}, "wrapStrategy,verticalAlignment")]
    for text, colors in (("INFO", GREY), ("REVIEW", AMBER), ("ACTION", RED)):
        req.append(_cf_text(s, 4, 5, 1, text, colors[0], colors[1]))
    steps["System Health"] = req
    return steps


def _chart(kind: str, title: str, sheet: int, domain: tuple, series: list[tuple], anchor: tuple, size=(580, 290), offset_x=8) -> dict:
    def src(rng_):
        r0, r1, c0, c1 = rng_
        return {"sourceRange": {"sources": [{"sheetId": sheet, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}]}}
    place = {"overlayPosition": {"anchorCell": {"sheetId": anchor[0], "rowIndex": anchor[1], "columnIndex": anchor[2]},
                                 "offsetXPixels": offset_x, "offsetYPixels": 6, "widthPixels": size[0], "heightPixels": size[1]}}
    if kind == "PIE":
        spec = {"title": title, "pieChart": {"legendPosition": "RIGHT_LEGEND", "domain": src(domain), "series": src(series[0])}}
    else:
        spec = {"title": title, "basicChart": {"chartType": kind, "legendPosition": "NO_LEGEND", "headerCount": 0,
                                               "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
                                               "domains": [{"domain": src(domain)}],
                                               "series": [{"series": src(s), "targetAxis": "LEFT_AXIS"} for s in series]}}
    return {"addChart": {"chart": {"spec": spec, "position": place}}}


def _chart_requests(ids: dict[str, int], n_coins: int) -> list[dict]:
    d = ids["Dashboard"]
    eqc = lambda k: EQ0 + list(EQ).index(k)
    return [
        _chart("LINE", "Equity curve (cumulative net P&L, $)", d, (4, 304, eqc("n"), eqc("n") + 1), [(4, 304, eqc("cum"), eqc("cum") + 1)], (d, 34, 0), size=(680, 285)),
        _chart("COLUMN", "Total Gross P&L by Coin ($)", d, (4, 24, 4, 5), [(4, 24, 8, 9)], (d, 34, 4), size=(650, 285)),
        _chart("PIE", "How trades ended (Outcomes)", d, (4, 8, OC0, OC0 + 1), [(4, 8, OC0 + 1, OC0 + 2)], (d, 50, 0), size=(680, 285)),
        _chart("COLUMN", "Daily net P&L ($)", d, (4, 35, DY0, DY0 + 1), [(4, 35, DY0 + 3, DY0 + 4)], (d, 50, 4), size=(650, 285)),
    ]


def ensure_layout(coins: list[str], force: bool = False) -> bool:
    """Create/refresh the 3 tabs once per layout version + coin list. Safe to call on every run."""
    gs = _gs()
    if not gs._client():
        return False
    marker = f"layout {LAYOUT_VERSION}-" + ".".join(c.split("-")[0] for c in coins)[:80]
    try:
        got = gs._request("GET", "values/" + quote(f"'Dashboard'!{MARKER_CELL}", safe="!'")).get("values", [[""]])
        if not force and got and got[0] and got[0][0] == marker:
            return True
    except Exception:
        pass

    def meta():
        m = gs._request("GET", "?fields=sheets(properties(sheetId,title),conditionalFormats,charts(chartId),bandedRanges(bandedRangeId))")
        return {s["properties"]["title"]: s for s in m.get("sheets", [])}

    state = meta()
    missing = [t for t in TABS_ORDER if t not in state]
    if missing:
        _safe("create tabs", lambda: gs._request("POST", ":batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]}))
        state = meta()
    ids = {t: int(state[t]["properties"]["sheetId"]) for t in TABS_ORDER if t in state}
    if len(ids) < len(TABS_ORDER):
        print(f"[!] Sheet layout: tabs still missing: {sorted(set(TABS_ORDER) - set(ids))}")
        return False

    grid_expand = [
        {"updateSheetProperties": {"properties": {"sheetId": ids["Dashboard"], "gridProperties": {"rowCount": 2000, "columnCount": 65}}, "fields": "gridProperties(rowCount,columnCount)"}},
        {"updateSheetProperties": {"properties": {"sheetId": ids["Positions"], "gridProperties": {"rowCount": 2000, "columnCount": 34}}, "fields": "gridProperties(rowCount,columnCount)"}},
        {"updateSheetProperties": {"properties": {"sheetId": ids["System Health"], "gridProperties": {"rowCount": 2000, "columnCount": 7}}, "fields": "gridProperties(rowCount,columnCount)"}},
        {"updateDimensionProperties": {"range": {"sheetId": ids["Dashboard"], "dimension": "COLUMNS", "startIndex": 0, "endIndex": 65},
                                       "properties": {"hiddenByUser": False}, "fields": "hiddenByUser"}},
    ]
    _safe("expand grid dimensions", lambda: gs._request("POST", ":batchUpdate", json={"requests": grid_expand}))

    cleanup = [{"unmergeCells": {"range": {"sheetId": ids["Dashboard"]}}}]
    for t in TABS_ORDER:
        sheet = state[t]
        for i in reversed(range(len(sheet.get("conditionalFormats", [])))):
            cleanup.append({"deleteConditionalFormatRule": {"sheetId": ids[t], "index": i}})
        for band in sheet.get("bandedRanges", []):
            cleanup.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})
        for ch in sheet.get("charts", []):
            cleanup.append({"deleteEmbeddedObject": {"objectId": ch["chartId"]}})
    cleanup.append({"updateSpreadsheetProperties": {"properties": {"timeZone": "Asia/Karachi"}, "fields": "timeZone"}})
    _safe("cleanup old rules/charts", lambda: gs._request("POST", ":batchUpdate", json={"requests": cleanup}))
    _safe("clear Dashboard", lambda: gs._request("POST", "values/" + quote("'Dashboard'", safe="'") + ":clear", json={}))

    L = col_letter
    blocks = [
        ("A1", dashboard_values()),
        ("E1", visible_right_tables(coins)),
        (f"{L(EQ0)}1", equity_block()),
        (f"{L(BC0)}1", by_coin_block(coins)),
        (f"{L(DY0)}1", daily_block()),
        (f"{L(OC0)}1", outcome_block()),
        (f"{L(SD0)}1", side_block()),
    ]
    for anchor, vals in blocks:
        _safe(f"write Dashboard {anchor}", lambda anchor=anchor, vals=vals: _put("Dashboard", vals, anchor=anchor))
    for label, reqs in _format_requests(ids, coins).items():
        _safe(f"format {label}", lambda reqs=reqs: gs._request("POST", ":batchUpdate", json={"requests": reqs}))
    _safe("charts", lambda: gs._request("POST", ":batchUpdate", json={"requests": _chart_requests(ids, len(coins))}))
    fit = [{"autoResizeDimensions": {"dimensions": {"sheetId": ids[t], "dimension": "ROWS", "startIndex": 1, "endIndex": 300}}} for t in ("Positions", "System Health")]
    _safe("auto-fit rows", lambda: gs._request("POST", ":batchUpdate", json={"requests": fit}))
    order = [{"updateSheetProperties": {"properties": {"sheetId": ids[t], "index": i}, "fields": "index"}} for i, t in enumerate(TABS_ORDER)]
    _safe("tab order", lambda: gs._request("POST", ":batchUpdate", json={"requests": order}))
    extras = [int(state[t]["properties"]["sheetId"]) for t in state if t not in TABS_ORDER]
    if extras:
        _safe("remove extra tabs", lambda: gs._request("POST", ":batchUpdate", json={"requests": [{"deleteSheet": {"sheetId": i}} for i in extras]}))
    _safe("marker", lambda: gs._request("PUT", "values/" + quote(f"'Dashboard'!{MARKER_CELL}", safe="!'") + "?valueInputOption=RAW", json={"values": [[marker]]}))
    return True
