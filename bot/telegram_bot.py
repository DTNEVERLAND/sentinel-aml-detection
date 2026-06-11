#!/usr/bin/env python3
"""ChainGuard Telegram bot — the P2P scorer where retail traders already are.

A retail seller is on their phone, inside the exchange app, the moment they
decide to trade. They will never open a GitHub Pages URL — but they are
already in Telegram scam-warning groups. This bot meets them there: send five
numbers from the buyer's listing, get a plain SAFE / CAUTION / DO-NOT-TRADE
verdict back instantly.

It reuses the exact same scoring engine as the CLI and the web tool
(core/p2p_scorer.py) — one source of truth, four interfaces.

The message-parsing and verdict-formatting are pure functions (no I/O), so they
are unit-tested offline in tests/test_bot.py without a live bot. Only main()
needs a token and network.

Run:
    BOT_TOKEN=123:abc  python bot/telegram_bot.py
Get a token from @BotFather in Telegram first. Dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# Make the repo root importable whether run as `python bot/telegram_bot.py`
# or `python -m bot.telegram_bot`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import AppConfig
from core.p2p_scorer import P2PTraderMetadata, evaluate_counterparty_risk

API = "https://api.telegram.org/bot{token}/{method}"

HELP = (
    "🛡️ *ChainGuard — P2P buyer check*\n\n"
    "Before you sell crypto P2P, check the buyer so dirty money doesn't get "
    "*your* bank account frozen.\n\n"
    "Send me *5 numbers* in this order, separated by spaces:\n"
    "`<their price> <market price> <account age days> <30-day orders> <completion %>`\n\n"
    "*Example:*\n"
    "`4.305 4.20 9 280 99`\n\n"
    "Find them on the buyer's P2P profile (tap their name). I reply instantly. "
    "Nothing you send is stored.\n\n"
    "_This is a risk signal, not financial or legal advice._"
)

_VERDICT = {
    "CLEAN": ("✅ *LIKELY SAFE*",
              "No mule fingerprint on the public stats. Still keep the trade "
              "inside the platform's escrow — never release coins outside the app."),
    "SUSPICIOUS": ("⚠️ *CAUTION — CONSIDER SKIPPING*",
                   "This buyer shows mule traits. For a few cents of price "
                   "difference it is not worth a frozen account — pick another buyer."),
    "HIGH_RISK": ("⛔ *DO NOT TRADE*",
                  "Strong match for a dirty-fiat flush pattern. Trading here "
                  "risks your bank account being frozen. Walk away."),
}


def parse_numbers(text: str) -> list[float] | None:
    """Extract the 5 numeric fields from a free-form message.

    Accepts extra words/punctuation; returns None unless exactly 5 numbers are
    present (so we can give a helpful error instead of mis-scoring)."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if len(nums) != 5:
        return None
    return [float(n) for n in nums]


def score_message(text: str, config: AppConfig | None = None) -> str:
    """Pure: free-form message -> reply text. No I/O. Unit-tested offline."""
    config = config or AppConfig.retail_safe()
    nums = parse_numbers(text)
    if nums is None:
        return ("I need exactly *5 numbers*:\n"
                "`<price> <market> <age days> <30d orders> <completion %>`\n"
                "Example: `4.305 4.20 9 280 99`\n\nSend /help for details.")

    ad, mid, age_f, orders_f, finish = nums
    try:
        result = evaluate_counterparty_risk(
            P2PTraderMetadata(
                trader_id="buyer",
                advertised_price_fiat=ad,
                market_mid_price_fiat=mid,
                account_age_days=int(age_f),
                total_orders_30d=int(orders_f),
                completion_rate_pct=finish,
            ),
            config,
        )
    except ValueError as e:
        return f"⚠️ Those numbers don't look right: {e}\n\nSend /help for the format."

    return format_verdict(result)


def format_verdict(result: dict) -> str:
    """Pure: scorer output -> Telegram-markdown reply."""
    headline, advice = _VERDICT[result["category"]]
    sig = result["signals"]
    lines = [
        headline,
        f"risk score *{result['risk_score']:.2f} / 1.00*",
        "",
        advice,
    ]
    if result["reasons"]:
        lines.append("")
        lines.append("*Why:*")
        lines += [f"• {r}" for r in result["reasons"]]
    lines += [
        "",
        f"`premium {sig['premium_delta']*100:+.2f}%  "
        f"{sig['daily_velocity']:.0f}/day  "
        f"{'new' if sig['is_new_account'] else 'aged'}  "
        f"finish {sig['completion_rate_pct']:.1f}%`",
    ]
    return "\n".join(lines)


# ── Telegram I/O (needs BOT_TOKEN + network) ────────────────────────────────


def _api(token: str, method: str, **params) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=65) as r:
        return json.load(r)


def _send(token: str, chat_id: int, text: str) -> None:
    try:
        _api(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:  # never let one bad send kill the loop
        print(f"send failed: {e}", file=sys.stderr)


def main() -> int:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("Set BOT_TOKEN (get one from @BotFather). Aborting.", file=sys.stderr)
        return 2

    print("ChainGuard bot polling… (Ctrl-C to stop)")
    offset = 0
    while True:
        try:
            resp = _api(token, "getUpdates", offset=offset, timeout=60)
        except Exception as e:
            print(f"poll error: {e}", file=sys.stderr)
            time.sleep(3)
            continue

        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            text = msg.get("text", "")
            chat = msg.get("chat", {}).get("id")
            if not chat:
                continue
            if text.startswith("/start") or text.startswith("/help"):
                _send(token, chat, HELP)
            else:
                _send(token, chat, score_message(text))


if __name__ == "__main__":
    raise SystemExit(main())
