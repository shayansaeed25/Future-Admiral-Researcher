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
