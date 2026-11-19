# Future Admiral Research Desk v5.0 — Live Audit, Sheet Coloring & Sync Upgrade (Roman Urdu)

## 1. Kal Aur Aaj (Nov 10–11) Ki Live Trades Ka Audit
- **Kal Ke CPI Dump Ke Winners:** Bot ne CPI risk-off aur ETF outflow ko pakad kar zabardast `SELL` winners diye:
  - `ETH-USDT SELL` -> **`TP3_HIT` (+$3.51)**
  - `UNI-USDT SELL` -> **`TP3_HIT` (+$2.49)**
  - `BCH-USDT SELL` -> **`TP2_HIT` (+$3.50, SL at Breakeven)**
  - `OP-USDT SELL` -> **`TP1_HIT` (+$1.64, SL at Breakeven)**
- **Aaj Ka `LTC-USDT` SL Hit (`-$2.06`) Aur `BTC-USDT` Entry (`73,237.7`) Kyun Hui Aur Kya Fix Kiya Gaya?**
  1. **`LTC-USDT SELL @ 50.36` (`pullback` setup -> `SL_HIT -$2.06`):**
     - Asal Wajah: Kal ke dump ke baad aaj 12:06 PKT par market 1H relief bounce le rahi thi (`1H MACD histogram` upar ja raha tha aur `RSI` 53+ par curl kar raha tha). Purane `_pullback` ne sirf EMA20 touch dekha magar yeh check nahi kiya ke 1H momentum tezi se bounce kar raha hai.
     - **v5.0 Fix (`Anti-Relief-Bounce Guard`):** Ab `signal_engine.py` mein agar 1H MACD momentum trade ke khilaf tezi se barh raha ho (`macd_delta < -0.12 * ATR` at `RSI >= 53`), to `pullback` SELL block ho jata hai (`1H counter-bounce momentum still expanding — awaiting clean rejection`).
  2. **`BTC-USDT SELL @ 73,237.7` (`breakout` setup on `6.1x` Climax Volume at `RSI 33.8`):**
     - Asal Wajah: Jab 1H candle `6.1x` climax volume ke sath giri to `1H RSI` pehle hi `33.8` (oversold) par aa chuka tha aur orderbook mein `+21%` buyers neeche absorb kar rahe thay. Climax candle ke bilkul bottom par breakout sell lene se price foran `73,818` par bounce kar gayi.
     - **v5.0 Fix (`Anti-Climax Exhaustion & Orderbook Absorption Guard`):** Ab agar breakout candle par climax volume (`>= 4.5x`) ho aur RSI pehle hi exhausted (`<= 35.5` for SELL ya `>= 64.5` for BUY) ho, ya orderbook depth (`>= 18%`) breakout ke khilaf absorb kar rahi ho, to bot bottom/top chase nahi karega balki pullback ka wait karega.
  3. **Google Sheet Sync & Duplicate `closed_trades.jsonl` Fix:**
     - Jab `LTC-USDT` aaj `19:05:51 PKT` par `SL_HIT` hua, to Google Sheets API limit ki wajah se sheet mein Row 45 `PENDING` reh gayi thi.
     - Ab `google_sheets_sync.py` (`sync_open_positions`) har run par `closed_trades.jsonl` se match karta hai aur agar koi trade close ho chuki ho to Google Sheet ki row ko khud-ba-khud `SL_HIT` / `TP3_HIT` par heal kar deta hai. Sath hi `closed_trades.jsonl` ki 81 duplicate lines ko clean kar ke 41 unique closed trades par set kar diya gaya hai.

## 2. Google Sheet & Excel Coloring System Ka Mustaqil Ilaj (`Sheet Layout v6`)
- **Masla Kya Tha?**
  1. `Positions` tab mein Row 2 se Row 45 tak har trade ke peechay `#1F2A44` (dark navy blue) aur Row 46–57 par `#143F6D` (bright blue) background lag gaya tha kyunke Google Sheets `append` karte waqt Row 1 (Header) ka dark blue color neeche copy kar deta tha, aur `sheet_layout.py` data rows ka background white (`#FFFFFF`) par reset nahi kar raha tha.
  2. `Dashboard` tab mein sirf 9 columns (`A:I`) thay, jis ki wajah se `AA1..BE1` (charts data) aur `BJ1` (layout marker) `exceeds grid limits` error de kar fail ho rahe thay.
- **v5.0 (`sheet_layout.py` Layout v6) Mein Kya Kiya Gaya?**
  1. `ensure_layout()` sab se pehle `Dashboard` ko `65 columns x 2000 rows` aur `Positions` ko `34 columns x 2000 rows` par expand karta hai.
  2. Data rows (`Row 2..2000`) ka background explicitly **Pure White (`#FFFFFF`)** aur text **Dark Slate (`#1E293B`)** par set karta hai, aur `_fit_rows()` har nayi trade append hone par uska background white rakhta hai.
  3. Sirf `Trade` (`BUY`/`SELL`), `TP Status` (`HIT`/`PENDING`), aur `Position Status` (`TP3_HIT`, `SL_HIT`, `BE_EXIT`, `PENDING`) par saaf-suthray **Institutional Pill Badges** (Soft Emerald Green, Soft Rose Red, Soft Amber, Soft Sky Blue) lagaye gaye hain.


## v7.0 Update (Nov 19, 2026) — Security Hardening + Pullback SL Fix + Free Public Repo Setup

### 1. Security Fix (Discord Webhook Leak Khatam!)
- Pehle `cli/main.py` aur `tradingagents/dataflows/alerts.py` mein aapka purana Discord Webhook URL hardcoded tha — isi liye jab aapne repo Public ki thi, kisi ne code se webhook nikaal kar spam bhejna shuru kar diya tha.
- v7.0 mein dono files se hardcoded webhook bilkul hata diya gaya hai. Ab har key/webhook sirf GitHub Secrets (`os.getenv(...)`) se read hota hai.
- Naya `tests/test_security.py` bhi add kar diya gaya hai jo confirm karta hai ke poore repo mein koi bhi hardcoded webhook ya API key maujood nahi hai.

### 2. Repo Ko Safely PUBLIC Kaise Karein (100% Free Unlimited GitHub Actions Minutes)
1. **Sab se pehle purana Discord Webhook DELETE karein** (Discord Channel Settings -> Integrations -> Webhooks -> purana delete -> naya Webhook bana kar copy karein).
2. **GitHub -> Settings -> Secrets and variables -> Actions** mein `DISCORD_WEBHOOK` ko naye webhook URL se update karein (baaki saari secrets jaise `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_API_KEY` wahan pehle se encrypted aur 100% private hain — Public repo mein bhi koi inhein nahi dekh sakta).
3. **Nayi Clean Public Repo banayein** (behtar yeh hai ke purani git history mein purana webhook na rahe, is liye nayi Public repo bana kar v7.0 files upload karein aur Secrets add kar dein — ya purani repo Public karne se pehle purana Discord webhook delete karna laazmi hai).
4. **Result**: Standard `ubuntu-latest` runners par GitHub Actions **100% Free & Unlimited** chalenge (`$0` billing usage!).

### 3. Strategy Upgrades (6-Day Live Audit ke baad)
- **Pullback Max SL Cap (`2.65%`)**: `pullback` setups par 2.9%–3.2% wale bare stops block honge (`ADMIRAL_PULLBACK_MAX_SL_PCT=2.65`), jabke `smc_sweep` (80% win rate) apni `3.25%` limit par chalta rahega.
- **Correlated Stacking Cap (`ADMIRAL_MAX_SAME_DIRECTION=3`)**: Ek hi direction mein maximum 3 trades open hongi (pehle 4 thin) taake BTC ke ek bounce par 4 altcoins ek saath SL hit na karein.
