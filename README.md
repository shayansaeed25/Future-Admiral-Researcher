# Future Admiral — simple guide (Roman Urdu)

## Yeh bot kya karta hai
Har ghante GitHub par chalta hai (free). Top 20 coins dekhta hai. Teen cheezein milata hai:
1. **Technical**: 1D/4H trend + 4H pullback setup, stop-loss structure ke peeche, 3 targets.
2. **Institutional proxies**: ETF flows (BTC/ETH), OKX taker buying/selling, Coinbase premium, funding, open interest.
3. **Macro**: US10Y yield, dollar, oil, inflation expectations, VIX, Nasdaq, Fear&Greed, FOMC din. (Oil↑ → inflation↑ → Fed cuts door → crypto par dabao.)
Sab theek ho to hi **signal** deta hai. Setup nahi bana to kuch nahi deta (aur wajah batata hai).

## API kahan use hoti hai
Decision mein **koi LLM nahi**. LLM sirf (a) naye signal ka 2-3 line note, (b) roz ka ek macro brief likhta hai: roz 2-6 calls.
Baqi sab free public data hai. `GOOGLE_API_KEYS` mein apni saari keys comma se alag daal do, bot ghuma ghuma kar use karta hai.

## Tumhein kahan kya dikhega
- **Discord**: har 4 ghante ek "Market briefing" (har coin ka haal + macro + ETF) aur jab setup bane to **signal card**.
- **Google Sheet: sirf 3 tabs** (baqi sab khud delete ho jate hain):
  1. `Dashboard`: win rate, net P&L (fees ke baad), verdict, Settings (peele cells), 4 charts, aur neeche "Market now" (har coin ka haal aur kyun).
  2. `Positions`: har signal/trade. TP hit = hara, SL hit = laal, breakeven = peela, khula trade = neela. Text kat'ta nahi.
  3. `System Health`: data feeds ki report (ACTION = laal).

## Setup
1. Purani repo ki saari files delete karo, is zip ki files upload karo (push).
   Sheet saaf karni ho (naya shuru): Actions -> Run workflow -> `reset_sheet` = true (sirf ek baar).
2. GitHub → Settings → Secrets → Actions: `DISCORD_WEBHOOK`, `GOOGLE_API_KEYS`, (optional) `GROQ_API_KEYS`, `CRYPTOETF_API_KEY`, `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`.
3. Settings → Actions → General → Workflow permissions → **Read and write**.
4. Actions → "Future Admiral Hourly Bot" → Run workflow (ek baar haath se). Phir wo khud chalta rahega.
5. Pehle 4-6 hafte sirf paper. Real paise 30-40 band trades dekh kar.

## Macro data na aaye to
Actions -> "Future Admiral Macro Check" -> Run workflow: har source ka OK/FAIL dikhata hai.
Sab se pakka tareeqa: fred.stlouisfed.org par free account banao -> My Account -> API Keys -> key copy karo -> GitHub secret `FRED_API_KEY`.
Macro roz ek baar fetch hota hai (fail ho to 3 ghante baad dobara), is liye run lamba nahi hota.

## Sach
Is mein chhota edge hai (backtest mein ~0.03-0.1R per trade). Macro/ETF history ka backtest nahi ho sakta, wo forward paper se parkha jayega. Koi system profit ki guarantee nahi.
