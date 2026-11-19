# AI Provider Setup

The hourly scanner first runs a deterministic public-data screen; at most one eligible coin gets the full AI research graph per scheduled run. Gemini and Groq can each rotate through all nine configured account keys after key-specific quota errors, then the chain falls back Gemini → Groq → OpenRouter. Provider/model-wide overload responses skip same-provider keys because switching accounts will not fix that model capacity error. A seven-minute internal limit and eight-minute one-coin workflow safety stop still bound a run.

## What the latest run needs

The supplied Actions log shows Gemini and Groq returning quota/rate-limit responses and says OpenRouter has no key configured. To enable that third fallback:

1. Create an OpenRouter API key in your OpenRouter account.
2. In the GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Set the secret name to `OPENROUTER_API_KEY` and paste the key into the secret value.
4. Do not paste the key into Discord, a commit, this chat, or a project file.

The workflow already reads this secret. The current default fallback model is `meta-llama/llama-3.2-3b-instruct:free`; availability and rate limits for free models can still vary. A key enables the fallback but cannot guarantee that a provider is accepting requests at every moment.

The supplied run returned `401 User not found` for OpenRouter, so that secret is not authenticating to a usable OpenRouter user/key. Create a fresh API key while signed into the intended OpenRouter account, replace the repository's `OPENROUTER_API_KEY` secret, and do not include spaces or quotes around the key. The bot cannot repair an invalid provider credential itself.

## Why nine keys may still receive 429

Keys associated with the same Google Cloud project or the same Groq organization share provider quotas. More keys do not necessarily create more quota. The scanner rotates the starting key between coins/runs. If the keys share one project or organization quota, adding more keys will not remove that shared limit.

`GOOGLE_API_KEYS` and `GROQ_API_KEYS` are read as comma-separated lists from GitHub Actions secrets. Keep their values secret. The bot never prints or sends the API key values.

Provider limits can still be shared at project or organization level. The nine-key pool start rotates by ticker/run, preserving your separate-account fallback. The Qwen second full-graph retry has been removed to avoid replaying the whole research just to try another model on the same exhausted provider. If both Gemini and Groq pools fail, OpenRouter is still attempted. Keys under one shared project/organization may fail together; additional keys cannot override a shared quota.

Some Gemini `403 PERMISSION_DENIED` key failures can be due to key/project permissions. In September 2026 Google is requiring migration of standard Gemini API keys to authorization keys; confirm that each key is current and enabled for Gemini in AI Studio, and that the project/API restriction is correct. The Google Sheets service-account credential is separate from the Gemini key.

## Diagnostics and Discord

Discord receives one short message when a coin's analysis fails. Detailed provider and feed diagnostics are kept in `data_health.log`, the Google Sheets `System Health` tab (when configured), and the downloadable `future-admiral-diagnostics-<run id>` artifact attached to the GitHub Actions run for 30 days.

The scheduled market-data scan runs hourly; the full multi-agent AI graph runs no more than once per scheduled run, only when the derivatives score and coverage cross the existing evidence gate. Open positions are monitored each hour. Manual one-coin analysis remains available from the same workflow's **Run workflow** button and still runs the full research path.
# Optional free institutional-flow context

The bot can show daily spot-ETF net inflow/outflow context for BTC, ETH, SOL,
XRP, HYP, DOGE, LINK, AVAX, HBAR, LTC, BNB, DOT, and SUI. Create a free API
key at https://cryptoetf.today, then add it to GitHub repository **Settings →
Secrets and variables → Actions → New repository secret**:

- Name: `CRYPTOETF_API_KEY`
- Secret: paste the API key from your CryptoETF.today profile

The workflow passes this secret to the analysis automatically. No bot file
needs editing after the secret is added. Without the key, analysis still runs
and the card/sheet labels ETF flow as unavailable. Daily ETF flows are delayed
fund demand data; they do not prove live whale-wallet purchases, and they do
not alone trigger a BUY or SELL. The existing price/derivatives/research gates
remain responsible for the actual signal decision.
