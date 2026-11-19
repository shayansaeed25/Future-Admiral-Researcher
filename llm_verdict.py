"""Tiny LLM helper (optional).  The LLM NEVER decides a trade.

Uses: (1) a 2-3 line Roman-Urdu note on a signal the rules already approved
          (explaining the Whale/Institutional flow, RSI/VWAP/MACD setup, and Global News context),
      (2) one daily macro & geopolitical brief (covering Fed, CPI/PPI, US/Iran/Middle East, & Crypto).
Typical use: well under 10 calls per DAY.

Quota-friendly key handling: keys from GOOGLE_API_KEYS / GROQ_API_KEYS (comma separated, any
number) are rotated by the clock, so all 19 accounts share the load evenly.  A 429 moves on to the
next key (max 3 tries per call), then to Groq.  Any failure returns None - the bot just shows the
rule-based reasons.  ADMIRAL_LLM_VERDICT=0 turns everything off.
"""
from __future__ import annotations

import os
import time

import requests

_calls = [0]
GEMINI_MODEL_DEFAULT = "gemini-3.5-flash-lite"
GROQ_MODEL_DEFAULT = "llama-3.3-70b-versatile"


def _split(raw: str) -> list[str]:
    return [k.strip() for k in (raw or "").replace("\n", ",").split(",") if k.strip()]


def gemini_keys() -> list[str]:
    keys = _split(os.getenv("GOOGLE_API_KEYS", ""))
    single = os.getenv("GOOGLE_API_KEY", "").strip()
    return keys + ([single] if single and single not in keys else [])


def groq_keys() -> list[str]:
    keys = _split(os.getenv("GROQ_API_KEYS", ""))
    single = os.getenv("GROQ_API_KEY", "").strip()
    return keys + ([single] if single and single not in keys else [])


def enabled() -> bool:
    return os.getenv("ADMIRAL_LLM_VERDICT", "1").strip() != "0" and bool(gemini_keys() or groq_keys())


def _rotate(keys: list[str]) -> list[str]:
    if not keys:
        return []
    start = int(time.time() // 3600) % len(keys)  # a new starting key every hour
    return keys[start:] + keys[:start]


def _gemini(prompt: str, key: str, max_tokens: int) -> str | None:
    model = os.getenv("ADMIRAL_LLM_MODEL", GEMINI_MODEL_DEFAULT).strip()
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                      json={"contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}}, timeout=20)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return " ".join(p.get("text", "") for p in parts).strip() or None


def _groq(prompt: str, key: str, max_tokens: int) -> str | None:
    model = os.getenv("ADMIRAL_GROQ_MODEL", GROQ_MODEL_DEFAULT).strip()
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": max_tokens, "temperature": 0.3}, timeout=20)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip() or None


def generate(prompt: str, max_tokens: int = 700, max_calls: int | None = None) -> str | None:
    """One answer, trying at most 3 keys (Gemini first, then Groq). Never raises."""
    if not enabled():
        return None
    limit = int(float(os.getenv("ADMIRAL_LLM_MAX_CALLS", "4") or 4)) if max_calls is None else max_calls
    plan = [("gemini", k) for k in _rotate(gemini_keys())[:2]] + [("groq", k) for k in _rotate(groq_keys())[:1]]
    for provider, key in plan:
        if _calls[0] >= limit:
            return None
        _calls[0] += 1
        try:
            text = (_gemini if provider == "gemini" else _groq)(prompt, key, max_tokens)
            if text:
                return " ".join(text.split())[:700]
        except Exception as exc:
            print(f"[!] LLM ({provider}) skipped: {type(exc).__name__} {str(exc)[:90]}")
    return None


def get_verdict(ticker: str, action: str, score: float, setup: str, reasons: list[str], levels: dict) -> str | None:
    prompt = (
        "You write the short note for a 1H crypto signal that our institutional rules engine has ALREADY approved. "
        "Write 2-3 short sentences in Roman Urdu (Urdu in English letters). Explain the institutional/whale flow & "
        "RSI/VWAP/MACD setup, and name the single biggest risk. Use ONLY the facts below, never invent prices or news, "
        "never change the direction, and do not promise profit.\n"
        f"Coin: {ticker}\nSide: {action}\nQuality score: {score:.0f}/100\nSetup: {setup}\n"
        f"Reasons: {'; '.join(reasons)}\n"
        f"Entry {levels.get('entry'):.6g}, stop {levels.get('sl'):.6g} ({levels.get('risk_pct', 0):.1f}% away), "
        f"targets {levels.get('tp1'):.6g} / {levels.get('tp2'):.6g} / {levels.get('tp3'):.6g}.\n")
    return generate(prompt)


def get_macro_brief(macro_notes: list[str], headlines: list[str]) -> str | None:
    if not headlines and not macro_notes:
        return None
    prompt = (
        "You are a global macro & geopolitical desk analyst. In 3 short Roman Urdu sentences (Urdu in English letters) "
        "say what today's CPI/PPI/Fed/yield/dollar, US/Iran/Middle East geopolitics, and crypto institutional headlines "
        "mean for crypto risk appetite (risk-on, risk-off or mixed). Use ONLY the data below, no invented numbers, "
        "no trade calls.\n"
        f"Market data: {'; '.join(macro_notes) or 'n/a'}\nHeadlines:\n" + "\n".join(headlines[:12]))
    return generate(prompt, max_tokens=800)
