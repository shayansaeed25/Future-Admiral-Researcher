"""Google Sheets ledger plus persistent two-way open-position & state tracker."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from admiral_utils import PKT, format_pkt, normalize_ticker, now_pkt
from paper_trading import initialize_paper_tracking

SIGNAL_HEADERS = ["Run ID", "Time (PKT)", "Source", "Coin", "Result", "Trade", "Market Price (USDT)", "Entry Price (USDT)", "Stop Loss (USDT)", "TP1 Price (USDT)", "TP1 Status", "TP2 Price (USDT)", "TP2 Status", "TP3 Price (USDT)", "TP3 Status", "Position Status", "Score", "Data Coverage", "Reason", "SL/TP Basis", "Observed Low (USDT)", "Observed High (USDT)", "Paper Notional (USDT)", "Paper Quantity", "Paper Gross P&L (USDT)", "Paper Return (%)", "Signal ID", "ETF Flow 1d (USD M)", "ETF Flow 7d (USD M)", "Institutional Flow Bias", "Flow Data Date", "Flow Data Source", "Overall Trend", "Overall Momentum", "Accumulation / Distribution", "1H Trend", "4H Trend", "1D Trend", "Taker Buy/Sell Proxy", "Order-book Imbalance Proxy"]
POSITION_HEADERS = ["Signal ID", "Created (PKT)", "Source", "Coin", "Trade", "Entry Price (USDT)", "Stop Loss (USDT)", "TP1 Price (USDT)", "TP1 Status", "TP2 Price (USDT)", "TP2 Status", "TP3 Price (USDT)", "TP3 Status", "Position Status", "Latest Price (USDT)", "Last Checked (PKT)", "Exit Price (USDT)", "Closed (PKT)", "Filled Quantity (manual)", "Fill Status (manual)", "Notes", "Score", "Data Coverage", "SL/TP Basis", "Observed Low (USDT)", "Observed High (USDT)", "Paper Notional (USDT)", "Paper Initial Qty", "Paper Remaining Qty", "Realized Gross P&L (USDT)", "Unrealized Gross P&L (USDT)", "Total Gross P&L (USDT)", "Gross Return (%)", "Paper P&L Method"]
HEALTH_HEADERS = ["Run ID", "Time (PKT)", "Source", "Coin", "Severity", "Category", "Diagnostics"]
OPEN_POSITION_STATUSES = {"PENDING", "OPEN", "TP1_HIT", "TP2_HIT"}
CLOSED_POSITION_STATUSES = {"TP3_HIT", "SL_HIT", "BE_EXIT", "TIME_EXIT"}
STATE_BACKUP_CELL = "BJ2"

DASHBOARD_ROWS = [
    ["Future Admiral · Natija (har trade $100 paper, fees ke baghair)", "Value"],
    ["Kul trades (band + khuli)", '=COUNT(Positions!AF2:AF2000)'],
    ["Band (closed) trades", '=SUMPRODUCT(((Positions!N2:N2000="TP3_HIT")+(Positions!N2:N2000="SL_HIT")+(Positions!N2:N2000="BE_EXIT")+(Positions!N2:N2000="TIME_EXIT"))*ISNUMBER(Positions!AF2:AF2000))'],
    ["Khuli trades abhi", '=COUNTIF(Positions!N2:N2000,"PENDING")+COUNTIF(Positions!N2:N2000,"TP1_HIT")+COUNTIF(Positions!N2:N2000,"TP2_HIT")'],
    ["Jeetne wale (profit > 0, breakeven ka chhota profit shamil)", '=SUMPRODUCT(((Positions!N2:N2000="TP3_HIT")+(Positions!N2:N2000="SL_HIT")+(Positions!N2:N2000="BE_EXIT")+(Positions!N2:N2000="TIME_EXIT"))*ISNUMBER(Positions!AF2:AF2000)*(Positions!AF2:AF2000>0))'],
    ["Harne wale", '=B3-B5'],
    ["Win rate", '=IFERROR(B5/B3,0)'],
    ["Band trades ka net P&L (USDT)", '=SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"TP3_HIT")+SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"SL_HIT")+SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"BE_EXIT")+SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"TIME_EXIT")'],
    ["Khuli trades ka P&L abhi (USDT)", '=SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"PENDING")+SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"TP1_HIT")+SUMIFS(Positions!AF2:AF2000,Positions!N2:N2000,"TP2_HIT")'],
    ["Har band trade ka average P&L (USDT)", '=IFERROR(B8/B3,0)'],
    ["Net P&L % (har trade $100 par)", '=IFERROR(B8/(B3*100),0)'],
    ["Stop-loss hue", '=SUMPRODUCT((Positions!N2:N2000="SL_HIT")*ISNUMBER(Positions!AF2:AF2000))'],
    ["TP3 tak pohnche", '=COUNTIF(Positions!N2:N2000,"TP3_HIT")'],
    ["Breakeven / time exit", '=COUNTIF(Positions!N2:N2000,"BE_EXIT")+COUNTIF(Positions!N2:N2000,"TIME_EXIT")'],
    ["Note", "Market Board tab: abhi har coin ka haal. Paper numbers fees, spread, slippage, funding shamil nahi karte."],
]
_session: dict[str, Any] | None = None
_sheet_ids: dict[str, int] = {}
_legacy_migration_checked = False


def _column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _client():
    global _session
    if _session:
        return _session
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not spreadsheet_id or not credentials_json:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    credentials.refresh(Request())
    _session = {"id": spreadsheet_id, "credentials": credentials, "token": credentials.token, "headers": {"Authorization": f"Bearer {credentials.token}"}}
    return _session


def _url(path: str) -> str:
    client = _client()
    if not client:
        raise RuntimeError("Google Sheets credentials are not configured")
    separator = "" if path.startswith(":") else "/"
    return f"https://sheets.googleapis.com/v4/spreadsheets/{client['id']}{separator}{path}"


def _request(method: str, path: str, **kwargs):
    client = _client()
    if client["credentials"] and getattr(client["credentials"], "expired", False):
        from google.auth.transport.requests import Request
        client["credentials"].refresh(Request())
        client["token"] = client["credentials"].token
        client["headers"] = {"Authorization": f"Bearer {client['token']}"}
    response = requests.request(method, _url(path), headers=client.get("headers", {}), timeout=20, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


_RESET_DONE = [False]


def _ensure_tabs() -> None:
    global _sheet_ids
    metadata = _request("GET", "?fields=sheets(properties(sheetId,title))")
    sheets = {item["properties"]["title"]: item["properties"]["sheetId"] for item in metadata.get("sheets", [])}
    missing = [name for name in ("Positions", "System Health") if name not in sheets]
    if missing:
        result = _request("POST", ":batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": name}}} for name in missing]})
        for item in result.get("replies", []):
            prop = item.get("addSheet", {}).get("properties", {})
            if prop.get("title"):
                sheets[prop["title"]] = prop["sheetId"]
    force_layout = False
    if os.getenv("ADMIRAL_RESET_SHEET", "0") == "1" and not _RESET_DONE[0]:
        for name in ("Positions", "System Health"):
            _request("POST", "values/" + quote(f"'{name}'", safe="'") + ":clear", json={})
        _RESET_DONE[0] = True
        force_layout = True
        print("[*] ADMIRAL_RESET_SHEET=1: Positions and System Health were cleared.")
    for name, headers in (("Positions", POSITION_HEADERS), ("System Health", HEALTH_HEADERS)):
        encoded = quote(f"'{name}'!A1:{_column_letter(len(headers))}1", safe="!'")
        result = _request("GET", f"values/{encoded}")
        if result.get("values", [[]])[0] != headers:
            target = quote(f"'{name}'!A1", safe="!'")
            _request("PUT", f"values/{target}?valueInputOption=RAW", json={"values": [headers]})
    _move_legacy_health_rows()
    try:
        import sheet_layout
        from watchlist_manager import load_watchlist
        sheet_layout.ensure_layout([c["ticker"] for c in load_watchlist()], force=force_layout)
        refreshed = _request("GET", "?fields=sheets(properties(sheetId,title))")
        _sheet_ids = {item["properties"]["title"]: int(item["properties"]["sheetId"]) for item in refreshed.get("sheets", [])}
    except Exception as exc:
        print(f"[!] Sheet layout skipped: {type(exc).__name__}: {str(exc)[:160]}")


def prepare() -> bool:
    try:
        if not _client():
            return False
        _ensure_tabs()
        return True
    except Exception as exc:
        print(f"[!] Sheet prepare failed: {type(exc).__name__}: {str(exc)[:160]}")
        return False


def _append(tab: str, values: list[Any]) -> None:
    target = quote(f"'{tab}'!A1", safe="!'")
    result = _request("POST", f"values/{target}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS", json={"values": [values]})
    updated_range = result.get("updates", {}).get("updatedRange", "")
    match = re.search(r"![A-Z]+(\d+):[A-Z]+(\d+)$", updated_range)
    if match:
        _fit_rows(tab, int(match.group(1)) - 1, int(match.group(2)))


def _upsert_scan_row(row: list[Any]) -> None:
    return None


def _fit_rows(tab: str, start_index: int, end_index: int) -> None:
    """Ensure newly appended data rows never inherit the Row 1 dark navy header fill (#1F2A44)."""
    if start_index < 1:
        return None
    try:
        sheet_id = _sheet_ids.get(tab)
        if sheet_id is None:
            return None
        _request("POST", ":batchUpdate", json={"requests": [{
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": start_index, "endRowIndex": end_index, "startColumnIndex": 0, "endColumnIndex": 34 if tab == "Positions" else 7},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                    "textFormat": {"foregroundColor": {"red": 0.118, "green": 0.161, "blue": 0.231}, "bold": False, "fontFamily": "Arial", "fontSize": 10},
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        }]})
    except Exception:
        pass
    return None


def _load_closed_lookup() -> dict[str, dict[str, Any]]:
    """Load closed trades from closed_trades.jsonl so sync_open_positions never resurrects an already-closed trade."""
    lookup: dict[str, dict[str, Any]] = {}
    try:
        if not os.path.exists("closed_trades.jsonl"):
            return lookup
        with open("closed_trades.jsonl", "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                sid = str(row.get("signal_id") or "").strip()
                if sid:
                    lookup[f"id:{sid}"] = row
                ticker = normalize_ticker(str(row.get("ticker") or ""))
                entry = row.get("entry")
                if ticker and entry is not None:
                    lookup[f"te:{ticker}:{round(float(entry), 6)}"] = row
    except Exception:
        pass
    return lookup


def _position_rows():
    target = quote(f"'Positions'!A1:{_column_letter(len(POSITION_HEADERS))}", safe="!'")
    return _request("GET", f"values/{target}").get("values", [])


def _parse_pkt_to_utc_iso(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()
    if "T" in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    cleaned = text.replace("PKT", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = PKT.localize(datetime.strptime(cleaned, fmt))
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return datetime.now(timezone.utc).isoformat()


def _float_cell(row: list[Any], idx: int, default: float | None = None) -> float | None:
    if idx >= len(row) or row[idx] in (None, ""):
        return default
    try:
        return float(str(row[idx]).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return default


def sync_open_positions(positions: dict[str, Any]) -> dict[str, Any]:
    """Two-way sync between local positions and Google Sheets 'Positions' tab."""
    try:
        if not _client():
            return positions
        rows = _position_rows()
        if not rows or len(rows) <= 1:
            return positions

        seen_open_coins: dict[str, str] = {}
        seen_signal_ids: set[str] = set()
        duplicate_row_indices: list[int] = []
        reconciled: dict[str, dict[str, Any]] = {}
        closed_lookup = _load_closed_lookup()

        for row_idx_0, row in enumerate(rows[1:], start=1):
            if not row or len(row) < 6:
                continue
            signal_id = str(row[0] or "").strip()
            ticker = normalize_ticker(str(row[3] if len(row) > 3 else ""))
            action = str(row[4] if len(row) > 4 else "").strip().upper()
            status = str(row[13] if len(row) > 13 and row[13] else "PENDING").strip().upper()
            if not signal_id or not ticker or action not in ("BUY", "SELL"):
                continue

            if status in CLOSED_POSITION_STATUSES:
                positions.pop(signal_id, None)
                continue

            if status not in OPEN_POSITION_STATUSES:
                continue

            entry_val = _float_cell(row, 5)
            closed_rec = closed_lookup.get(f"id:{signal_id}") or (
                closed_lookup.get(f"te:{ticker}:{round(float(entry_val), 6)}") if entry_val is not None else None
            )
            if closed_rec and str(closed_rec.get("status", "")).upper() in CLOSED_POSITION_STATUSES:
                positions.pop(signal_id, None)
                try:
                    c_status = str(closed_rec["status"]).upper()
                    c_exit = closed_rec.get("exit_price") or _float_cell(row, 6) or entry_val or ""
                    c_pnl = closed_rec.get("pnl_usdt", "")
                    c_ret = (float(c_pnl) / 100.0) if isinstance(c_pnl, (int, float)) else ""
                    c_time = format_pkt(datetime.fromisoformat(str(closed_rec["closed_utc"]).replace("Z", "+00:00"))) if closed_rec.get("closed_utc") else format_pkt()
                    healed = list(row) + [""] * max(0, len(POSITION_HEADERS) - len(row))
                    healed[13] = c_status
                    healed[14] = c_exit
                    healed[15] = c_time
                    healed[16] = c_exit
                    healed[17] = c_time
                    healed[28] = 0.0
                    if c_pnl != "":
                        healed[29] = c_pnl
                        healed[30] = 0.0
                        healed[31] = c_pnl
                        healed[32] = c_ret
                    row_no = row_idx_0 + 1
                    target = quote(f"'Positions'!A{row_no}:{_column_letter(len(POSITION_HEADERS))}{row_no}", safe="!'")
                    _request("PUT", f"values/{target}?valueInputOption=RAW", json={"values": [healed[:len(POSITION_HEADERS)]]})
                except Exception:
                    pass
                continue

            if ticker in seen_open_coins or signal_id in seen_signal_ids:
                duplicate_row_indices.append(row_idx_0)
                if signal_id != seen_open_coins.get(ticker):
                    positions.pop(signal_id, None)
                continue

            seen_open_coins[ticker] = signal_id
            seen_signal_ids.add(signal_id)

            existing = dict(positions.get(signal_id) or {})
            entry = _float_cell(row, 5, existing.get("entry"))
            sl = _float_cell(row, 6, existing.get("sl"))
            tp1 = _float_cell(row, 7, existing.get("tp1"))
            tp2 = _float_cell(row, 9, existing.get("tp2"))
            tp3 = _float_cell(row, 11, existing.get("tp3"))
            if not entry or not sl or not tp1:
                continue

            tp1_status = str(row[8] if len(row) > 8 and row[8] else existing.get("tp1_status", "PENDING")).strip().upper()
            tp2_status = str(row[10] if len(row) > 10 and row[10] else existing.get("tp2_status", "PENDING")).strip().upper()
            tp3_status = str(row[12] if len(row) > 12 and row[12] else existing.get("tp3_status", "PENDING")).strip().upper()
            created_utc = existing.get("created_at_utc") or _parse_pkt_to_utc_iso(row[1] if len(row) > 1 else "")
            checked_utc = existing.get("last_checked_utc") or _parse_pkt_to_utc_iso(
                row[15] if len(row) > 15 and row[15] else (row[1] if len(row) > 1 else "")
            )
            at_be = bool(existing.get("sl_at_breakeven")) or (
                tp1_status == "HIT" and abs(sl - entry) <= max(1e-8, entry * 0.0005)
            )
            pos = {
                **existing,
                "signal_id": signal_id,
                "source": str(row[2] if len(row) > 2 and row[2] else existing.get("source", "watchlist")).strip(),
                "ticker": ticker,
                "action": action,
                "entry": entry,
                "sl": sl,
                "tp": tp1,
                "tp1": tp1,
                "tp2": tp2 if tp2 is not None else entry + (1 if action == "BUY" else -1) * abs(tp1 - entry) * 2.0,
                "tp3": tp3 if tp3 is not None else entry + (1 if action == "BUY" else -1) * abs(tp1 - entry) * 3.0,
                "tp1_status": tp1_status,
                "tp2_status": tp2_status,
                "tp3_status": tp3_status,
                "status": "PENDING" if status == "OPEN" else status,
                "last_price": _float_cell(row, 14, entry),
                "created_at_utc": created_utc,
                "last_checked_utc": checked_utc,
                "score": _float_cell(row, 21, existing.get("score", 0.0)),
                "coverage": _float_cell(row, 22, existing.get("coverage", 1.0)),
                "risk_basis": str(row[23] if len(row) > 23 and row[23] else existing.get("risk_basis", "")),
                "sl_at_breakeven": at_be,
            }
            initialize_paper_tracking(pos)
            reconciled[signal_id] = pos

        if duplicate_row_indices:
            sheet_id = _sheet_ids.get("Positions")
            if sheet_id is None:
                meta = _request("GET", "?fields=sheets(properties(sheetId,title))")
                for item in meta.get("sheets", []):
                    prop = item.get("properties", {})
                    if prop.get("title") == "Positions":
                        sheet_id = int(prop["sheetId"])
                        _sheet_ids["Positions"] = sheet_id
                        break
            if sheet_id is not None:
                del_reqs = [
                    {"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": idx, "endIndex": idx + 1}}}
                    for idx in sorted(duplicate_row_indices, reverse=True)
                ]
                _request("POST", ":batchUpdate", json={"requests": del_reqs})
                print(f"[*] Cleaned {len(duplicate_row_indices)} duplicate open row(s) from Google Sheets Positions tab.")

        positions.clear()
        positions.update(reconciled)
        return positions
    except Exception as exc:
        print(f"[!] Sheet open-position sync skipped: {type(exc).__name__}: {str(exc)[:140]}")
        return positions


def load_cloud_state(state: dict[str, Any]) -> dict[str, Any]:
    try:
        if not _client():
            return state
        target = quote(f"'Dashboard'!{STATE_BACKUP_CELL}", safe="!'")
        got = _request("GET", f"values/{target}").get("values", [])
        if got and got[0] and got[0][0]:
            cloud = json.loads(str(got[0][0]))
            if isinstance(cloud, dict):
                for k in ("last_briefing_slot", "last_heartbeat_date", "last_feed_alert_utc", "paused_until"):
                    if cloud.get(k) and not state.get(k):
                        state[k] = cloud[k]
                if isinstance(cloud.get("loss_streak"), int) and not state.get("loss_streak"):
                    state["loss_streak"] = cloud["loss_streak"]
                if isinstance(cloud.get("cooldown"), dict):
                    merged_cd = dict(cloud["cooldown"])
                    merged_cd.update(state.get("cooldown") or {})
                    state["cooldown"] = merged_cd
                if isinstance(cloud.get("last_signal_candle_ts"), dict):
                    merged_ts = dict(cloud["last_signal_candle_ts"])
                    merged_ts.update(state.get("last_signal_candle_ts") or {})
                    state["last_signal_candle_ts"] = merged_ts
    except Exception:
        pass
    return state


def save_cloud_state(state: dict[str, Any]) -> bool:
    try:
        if not _client():
            return False
        payload = {
            "cooldown": state.get("cooldown") or {},
            "paused_until": state.get("paused_until") or "",
            "loss_streak": int(state.get("loss_streak") or 0),
            "last_briefing_slot": state.get("last_briefing_slot") or "",
            "last_heartbeat_date": state.get("last_heartbeat_date") or "",
            "last_feed_alert_utc": state.get("last_feed_alert_utc") or "",
            "last_signal_candle_ts": state.get("last_signal_candle_ts") or {},
        }
        target = quote(f"'Dashboard'!{STATE_BACKUP_CELL}", safe="!'")
        _request("PUT", f"values/{target}?valueInputOption=RAW", json={"values": [[json.dumps(payload, sort_keys=True)]]})
        return True
    except Exception:
        return False


def _resolve_notes(position: dict[str, Any], previous: list[Any]) -> str:
    """Keep the entry setup/grade/backtest note in Column U and append outcome status instead of overwriting with 'Checked OKX 1m candles...'."""
    prev_note = str(previous[20] if len(previous) > 20 and previous[20] else "").strip()
    if prev_note.startswith("Checked ") or prev_note.startswith("Candle range"):
        prev_note = ""
    incoming = str(position.get("notes") or position.get("reason") or "").strip()
    if incoming.startswith("Checked ") or incoming.startswith("Candle range"):
        incoming = ""
    base = prev_note or incoming
    status = str(position.get("status") or "").strip().upper()
    if status in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "BE_EXIT", "SL_HIT", "TIME_EXIT") and status not in base:
        return f"{base} · [{status}]" if base else f"[{status}]"
    return base or str(position.get("reason") or "")


def _save_position(position: dict[str, Any]) -> None:
    rows = _position_rows()
    signal_id = str(position.get("signal_id", ""))
    ticker = normalize_ticker(str(position.get("ticker", "")))
    status = str(position.get("status", "PENDING")).strip().upper()

    row_no = next((i + 1 for i, row in enumerate(rows[1:], start=1) if row and str(row[0]) == signal_id), None)
    if row_no is None and status in OPEN_POSITION_STATUSES and ticker:
        for i, row in enumerate(rows[1:], start=1):
            if len(row) > 13 and normalize_ticker(str(row[3])) == ticker and str(row[13]).strip().upper() in OPEN_POSITION_STATUSES:
                row_no = i + 1
                signal_id = str(row[0])
                position["signal_id"] = signal_id
                break

    previous = rows[row_no - 1] if row_no and row_no - 1 < len(rows) else []
    cells = [
        signal_id, format_pkt(datetime.fromisoformat(position["created_at_utc"])) if position.get("created_at_utc") else "", position.get("source", ""), ticker or position.get("ticker", ""),
        position.get("action", ""), position.get("entry", ""), position.get("sl", ""),
        position.get("tp1", position.get("tp", "")), position.get("tp1_status", "PENDING"),
        position.get("tp2", ""), position.get("tp2_status", "PENDING"), position.get("tp3", ""),
        position.get("tp3_status", "PENDING"), position.get("status", "PENDING"),
        position.get("last_price", ""), format_pkt(datetime.fromisoformat(position["last_checked_utc"])) if position.get("last_checked_utc") else "", position.get("exit_price", ""),
        format_pkt(datetime.fromisoformat(position["closed_at_utc"])) if position.get("closed_at_utc") else "", previous[18] if len(previous) > 18 else "",
        previous[19] if len(previous) > 19 else "not_connected", _resolve_notes(position, previous),
        position.get("score", ""), position.get("coverage", ""), position.get("risk_basis", ""),
        position.get("range_low", ""), position.get("range_high", ""),
        position.get("paper_notional_usdt", ""), position.get("paper_initial_qty", ""),
        position.get("paper_remaining_qty", ""), position.get("paper_realized_pnl_usdt", ""),
        position.get("paper_unrealized_pnl_usdt", ""), position.get("paper_total_pnl_usdt", ""),
        position.get("paper_return_pct", ""), position.get("paper_pnl_method", ""),
    ]
    if row_no:
        target = quote(f"'Positions'!A{row_no}:{_column_letter(len(POSITION_HEADERS))}{row_no}", safe="!'")
        _request("PUT", f"values/{target}?valueInputOption=RAW", json={"values": [cells]})
        _fit_rows("Positions", row_no - 1, row_no)
    else:
        _append("Positions", cells)


def record_scan_event(event: dict[str, Any]) -> bool:
    try:
        if not _client():
            return False
        _ensure_tabs()
        now_utc = datetime.now(timezone.utc).isoformat()
        if event.get("signal_id") and event.get("action") in ("BUY", "SELL"):
            _save_position({**event, "created_at_utc": event.get("created_at_utc") or now_utc,
                            "last_checked_utc": now_utc, "last_price": event.get("price"), "status": event.get("status", "PENDING")})
        return True
    except Exception as exc:
        print(f"[!] Google Sheets sync failed: {exc}")
        return False


def _health_severity(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ("critical:", "analysis/feed failed", "no price source", "google sheets", "no usable scoring value")):
        return "ACTION"
    if "recovered via" in lowered or any(token in lowered for token in ("429", "fred_api_key", "vendor 'fred'", "reddit rss", "yfinance", "optional macro_data")):
        return "INFO"
    return "REVIEW"


def _upsert_health_row(row: list[Any]) -> None:
    target = quote("'System Health'!A1:G", safe="!'")
    existing = _request("GET", f"values/{target}").get("values", [])
    run_id, ticker = str(row[0]), str(row[3])
    row_number = next((index + 1 for index, current in enumerate(existing[1:], start=1)
                       if len(current) > 3 and str(current[0]) == run_id and str(current[3]) == ticker), None)
    if row_number:
        destination = quote(f"'System Health'!A{row_number}:G{row_number}", safe="!'")
        _request("PUT", f"values/{destination}?valueInputOption=RAW", json={"values": [row]})
        _fit_rows("System Health", row_number - 1, row_number)
    else:
        _append("System Health", row)


def _move_legacy_health_rows() -> None:
    global _legacy_migration_checked
    _legacy_migration_checked = True
    return None


def record_health_event(event: dict[str, Any]) -> bool:
    try:
        if not _client():
            return False
        _ensure_tabs()
        message = "\n".join(str(item) for item in event.get("messages", []) if item)
        if not message:
            return True
        _upsert_health_row([
            event.get("run_id", ""), format_pkt(datetime.fromisoformat(event["time_utc"])) if event.get("time_utc") else format_pkt(),
            event.get("source", "watchlist"), event.get("ticker", ""),
            _health_severity(message), event.get("category", "FEED_DIAGNOSTICS"), message[:4000],
        ])
        return True
    except Exception as exc:
        print(f"[!] Google Sheets health logging failed: {exc}")
        return False


def update_position(event: dict[str, Any]) -> bool:
    try:
        if not _client():
            return False
        _ensure_tabs()
        now_utc = datetime.now(timezone.utc).isoformat()
        if event.get("signal_id"):
            _save_position({**event, "last_checked_utc": now_utc})
        return True
    except Exception as exc:
        print(f"[!] Google Sheets position update failed: {exc}")
        return False


def write_board(top_lines: list[list[Any]], rows: list[list[Any]]) -> bool:
    try:
        if not _client():
            return False
        import sheet_layout
        values = [list(r) + [""] * (9 - len(r)) for r in (top_lines + rows)]
        values += [[""] * 9 for _ in range(max(0, 36 - len(values)))]
        target = quote(f"'Dashboard'!A{sheet_layout.MARKET_ROW}", safe="!'")
        try:
            _request("PUT", f"values/{target}?valueInputOption=RAW", json={"values": values})
        except Exception:
            _ensure_tabs()
            _request("PUT", f"values/{target}?valueInputOption=RAW", json={"values": values})
        return True
    except Exception as exc:
        print(f"[!] Dashboard market block write failed: {exc}")
        return False
