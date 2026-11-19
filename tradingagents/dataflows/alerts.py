
import logging
import os
import requests
from datetime import datetime
from .market_time import format_pkt

logger = logging.getLogger(__name__)

DEFAULT_DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()


def send_discord_alert(ticker: str, decision: dict | str, report_summary: str, webhook_url: str = DEFAULT_DISCORD_WEBHOOK):
    """Dispatches a professional embed report to Discord channel."""
    if not webhook_url or "http" not in webhook_url:
        return

    action = "HOLD"
    if isinstance(decision, dict):
        action = decision.get("action", decision.get("recommendation", "HOLD"))
    elif isinstance(decision, str):
        action = decision

    action_str = str(action).upper()
    color_map = {
        "BUY": 0x00FF7F,   # Spring Green
        "LONG": 0x00FF7F,
        "SELL": 0xFF3333,  # Red
        "SHORT": 0xFF3333,
        "HOLD": 0xFFA500   # Orange / Neutral
    }
    color = color_map.get(action_str, 0xFFA500)

    payload = {
        "username": "Future Admiral Institutional Engine",
        "avatar_url": "https://raw.githubusercontent.com/shayansaeed565/Future-Admiral-Researcher/main/assets/analyst.png",
        "embeds": [
            {
                "title": f"⚡ SIGNAL ALERT: {ticker} ➔ [{action_str}]",
                "description": report_summary[:1800],
                "color": color,
                "fields": [
                    {"name": "Instrument", "value": f"`{ticker}`", "inline": True},
                    {"name": "Decision", "value": f"**{action_str}**", "inline": True},
                ],
                "footer": {"text": f"Admiral AI • Executed at {format_pkt()}"}
            }
        ]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        if r.status_code in [200, 204]:
            logger.info("Discord signal alert pushed successfully.")
        else:
            logger.warning(f"Discord responded with code {r.status_code}")
    except Exception as e:
        logger.error(f"Discord webhook error: {e}")
