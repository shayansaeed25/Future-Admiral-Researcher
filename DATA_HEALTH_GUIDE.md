# Data Health Alerts

Future Admiral har on-demand aur scheduled analysis ke data-source diagnostics ko data_health.log mein record karta hai. Watchlist coins aik aik karke chalte hain: har coin complete ho to us ka report GitHub par publish hota hai aur signal Discord par bheja jata hai; us ke baad agla coin shuru hota hai.

## Kahan dekhein

- Local `data_health.log` JSON-lines file mein poori diagnostic tafseel (Pakistan time, PKT); GitHub Actions ke har run se yeh 30 din ke downloadable artifact ke taur par save hoti hai.
- Google Sheets ke `System Health` tab mein feed diagnostics, agar Sheets connection available ho.
- GitHub Actions ke run log mein warning annotations.
- Discord par sirf mukhtasar final analysis/error alert aata hai; har provider-switch aur data feed ki tafseel Actions log/artifact aur Google Sheets ke System Health tab mein hoti hai. Successful card par istemal-shuda AI provider/fallback dikhaya jata hai.

## Status ka matlab

- `Recovered via ...`: primary source fail hua, lekin fallback ne data de diya. Analysis chali, par source warning log hui.
- `Unavailable` / `No usable scoring value`: us factor ke liye data nahin mila. Is surat mein coverage kam hogi aur trade gate signal ko rok sakta hai.
- `CRITICAL: no price source...`: kisi bhi configured price source se current price nahin mila; analysis ruki.
- Google Sheets update warning: ledger update fail hui ya Sheets secrets configured nahin.
- AI provider status: API ka remaining balance pehle se reliably nahin padha ja sakta. Provider jab rate limit/quota/error return karta hai tab bot har provider mein zyada se zyada do rotated keys azmata hai, phir Gemini -> Groq -> OpenRouter fallback karta hai. Aik hi Google project ya Groq organization ki kai keys aksar shared provider quota ko barhati nahin; 429 ki surat mein alag provider ka configured fallback zaroori hai.

Har coin ka full research 7-minute internal limit aur 8-minute workflow safety stop ke andar chalta hai.

## Kya karna hai

Alert mein endpoint/source aur error diya hoga. GitHub Actions run ke `Execute Future Admiral Engine` output mein usi source ko dhoondo. Yahoo ka unsupported crypto symbol fallback ke zariye recover ho sakta hai; alert recovered source ko bhi wazeh karta hai. Public Binance feeds ke liye Binance API key darkar nahin.

Agar Discord webhook hi fail ho, bot Discord par alert bhej nahin sakta; GitHub Actions mein `Discord notification delivery failed` error annotation aur log nazar aayega.
