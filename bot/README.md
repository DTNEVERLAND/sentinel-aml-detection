# ChainGuard Telegram Bot

The P2P scorer where retail traders actually are. A Malaysian P2P seller won't open a GitHub Pages URL mid-trade, but they're already in Telegram scam-warning groups. This bot meets them there: send five numbers, get a verdict.

Same scoring engine as the CLI and web tool (`core/p2p_scorer.py`) — one source of truth, four interfaces.

## What the user does

In Telegram, they message the bot five numbers from the buyer's P2P profile:

```
4.305 4.20 9 280 99
```
`<their price> <market price> <account age days> <30-day orders> <completion %>`

The bot replies instantly:

```
⛔ DO NOT TRADE
risk score 0.89 / 1.00

Strong match for a dirty-fiat flush pattern. Trading here risks your
bank account being frozen. Walk away.

Why:
• Buy ad priced 2.50% above market mid (allowed 0.80%) — classic flush pattern
• 31.1 orders/day against a threshold of 30 — industrial throughput on a 9-day account
```

Parsing is forgiving — `price 4.31, age 9d, 1,280 orders, 99%` works too. `/start` or `/help` shows instructions.

## Run it

1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the token.
2. Start the bot (stdlib only, no dependencies):

```bash
BOT_TOKEN=123456:ABC-your-token  python bot/telegram_bot.py
```

That's it — it long-polls, so no public URL or webhook is needed. Runs anywhere Python runs: your machine, a Raspberry Pi, a free-tier VM, etc. For 24/7 uptime put it on an always-on host (Railway / Fly.io / a cheap VPS) and set `BOT_TOKEN` as an environment variable.

## Design notes

- **Pure core, thin I/O.** `parse_numbers`, `score_message`, and `format_verdict` are pure functions with no network — they're unit-tested offline in [`tests/test_bot.py`](../tests/test_bot.py), so the verdict logic is proven without a live token. Only `main()`'s polling loop touches Telegram.
- **Stateless & private.** Nothing the user sends is stored or logged. Same determinism guarantee as the engine.
- **Retail-safe profile by default** — recall-first, because a missed mule costs a frozen account while a false alarm costs one skipped trade.

## Honest limitations (read before shipping to real users)

- **It's advice, not a guarantee.** A patient mule on an aged at-market account can still look CLEAN on public data (see the residual-evasion analysis in [`docs/p2p-scorer.md`](../docs/p2p-scorer.md)). The bot says so on every CLEAN verdict: *keep the trade in escrow regardless.*
- **Thresholds drift.** They're calibrated from a real market snapshot; re-pull `redteam/data/` and re-check `redteam/profile_compare.py` periodically.
- **You are putting a verdict in front of people making money decisions.** Keep the "risk signal, not financial/legal advice" disclaimer visible. Don't let it become something a user blames for a loss.
