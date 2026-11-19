"""Excel signal log."""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from pathlib import Path
from tradingagents.dataflows.market_time import format_pkt

XLSX_PATH = Path("future_admiral_signals.xlsx")

SIGNAL_HEADERS = [
    "Run ID", "Time (PKT)", "Source", "Coin", "Result", "Trade",
    "Price", "Entry", "SL", "TP1", "TP2", "TP3",
    "Status", "Buy Votes", "Sell Votes", "Reason",
]


def _get_workbook():
    if XLSX_PATH.exists():
        return openpyxl.load_workbook(XLSX_PATH)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Signals"
    ws.append(SIGNAL_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    wb.create_sheet("Dashboard")
    return wb


def record_signal_xlsx(event: dict) -> bool:
    try:
        wb = _get_workbook()
        ws = wb["Signals"]
        ws.append([
            event.get("run_id", ""),
            format_pkt(),
            event.get("source", "watchlist"),
            event.get("ticker", ""),
            event.get("event", "NO_TRADE"),
            event.get("action", "NO TRADE"),
            event.get("price", ""),
            event.get("entry", ""),
            event.get("sl", ""),
            event.get("tp1", ""),
            event.get("tp2", ""),
            event.get("tp3", ""),
            event.get("status", ""),
            event.get("buy_votes", ""),
            event.get("sell_votes", ""),
            str(event.get("reason", ""))[:500],
        ])
        for col in ws.columns:
            max_len = 0
            for cell in col:
                try:
                    max_len = max(max_len, len(str(cell.value or "")))
                except Exception:
                    pass
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)
        wb.save(XLSX_PATH)
        return True
    except Exception as e:
        print(f"[!] Excel export failed: {e}")
        return False


def update_dashboard() -> None:
    try:
        wb = _get_workbook()
        ws = wb["Dashboard"]
        for row in ws.iter_rows():
            for cell in row:
                cell.value = None
        signals_ws = wb["Signals"]
        rows = list(signals_ws.iter_rows(min_row=2, values_only=True))
        total = len(rows)
        buys = sum(1 for r in rows if len(r) > 5 and r[5] == "BUY")
        sells = sum(1 for r in rows if len(r) > 5 and r[5] == "SELL")
        no_sig = sum(1 for r in rows if len(r) > 4 and r[4] == "NO_SIGNAL")
        no_trd = sum(1 for r in rows if len(r) > 5 and r[5] == "NO TRADE")
        ws.append(["Future Admiral - Dashboard", ""])
        ws.append(["Total decisions", total])
        ws.append(["BUY signals", buys])
        ws.append(["SELL signals", sells])
        ws.append(["NO_SIGNAL", no_sig])
        ws.append(["NO_TRADE", no_trd])
        ws.append(["Last updated", format_pkt()])
        for cell in ws[1]:
            cell.font = Font(bold=True, size=14)
        ws.column_dimensions["A"].width = 32
        ws.column_dimensions["B"].width = 25
        wb.save(XLSX_PATH)
    except Exception as e:
        print(f"[!] Dashboard update failed: {e}")