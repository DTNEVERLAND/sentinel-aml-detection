#!/usr/bin/env python3
"""check_trader.py — pre-trade safety check for a P2P counterparty.

Run this before you sell crypto to someone on a P2P marketplace. Feed it the
numbers visible on their listing; it tells you whether trading with them risks
your bank account being frozen as part of a laundering chain.

Defaults to the recall-first RETAIL_SAFE profile: it would rather warn you about
a harmless trader (skip them, pick another) than wave through a mule (frozen
account). Pass --exchange for the balanced exchange-side profile.

Examples
--------
  py check_trader.py --premium 2.4 --age 8 --orders30 600 --finish 99
  py check_trader.py --ad-price 4.31 --mid 4.20 --age 400 --orders30 4500 --finish 99
  py check_trader.py --exchange --ad-price 4.45 --mid 4.20 --age 6 --orders30 720 --finish 97
"""

from __future__ import annotations

import argparse
import sys

from config_loader import AppConfig
from core.p2p_scorer import P2PTraderMetadata, evaluate_counterparty_risk

_VERDICT = {
    "CLEAN": (
        "✅ LIKELY SAFE",
        "No mule fingerprint on the public stats. Still use the platform's "
        "escrow and never release outside it.",
    ),
    "SUSPICIOUS": (
        "⚠️  CAUTION — CONSIDER SKIPPING",
        "This trader shows one or more mule traits. For a few ringgit of price "
        "difference it is not worth a frozen account. Prefer another counterparty.",
    ),
    "HIGH_RISK": (
        "⛔ DO NOT TRADE",
        "Strong match for a dirty-fiat flush pattern. Trading here risks your "
        "bank account being frozen when the chain is reported. Walk away.",
    ),
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pre-trade P2P counterparty safety check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Price: either give the premium directly, or give ad-price + mid.
    p.add_argument("--premium", type=float,
                   help="ad premium over market mid, in %% (e.g. 2.4)")
    p.add_argument("--ad-price", type=float,
                   help="trader's advertised price (use with --mid)")
    p.add_argument("--mid", type=float,
                   help="current market mid price (use with --ad-price)")

    p.add_argument("--age", type=int, required=True,
                   help="account age in days")
    p.add_argument("--orders30", type=int, required=True,
                   help="orders completed in the last 30 days")
    p.add_argument("--finish", type=float, required=True,
                   help="completion rate in %% (e.g. 99)")
    p.add_argument("--id", default="counterparty", help="label for the report")

    p.add_argument("--exchange", action="store_true",
                   help="use balanced exchange-side profile (default: retail-safe)")
    return p


def _resolve_prices(args) -> tuple[float, float]:
    """Return (ad_price, mid). Accepts either --premium or --ad-price/--mid."""
    if args.ad_price is not None and args.mid is not None:
        return args.ad_price, args.mid
    if args.premium is not None:
        # Synthesise a price pair on an arbitrary mid; only the ratio matters.
        mid = 100.0
        return mid * (1 + args.premium / 100.0), mid
    raise SystemExit(
        "error: provide either --premium, or both --ad-price and --mid"
    )


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    ad_price, mid = _resolve_prices(args)
    config = AppConfig() if args.exchange else AppConfig.retail_safe()
    profile = "exchange (balanced)" if args.exchange else "retail-safe (recall-first)"

    try:
        result = evaluate_counterparty_risk(
            P2PTraderMetadata(
                trader_id=args.id,
                advertised_price_fiat=ad_price,
                market_mid_price_fiat=mid,
                account_age_days=args.age,
                total_orders_30d=args.orders30,
                completion_rate_pct=args.finish,
            ),
            config,
        )
    except ValueError as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        return 2

    headline, advice = _VERDICT[result["category"]]
    sig = result["signals"]

    print()
    print(f"  Counterparty : {result['trader_id']}")
    print(f"  Profile      : {profile}")
    print(f"  Risk score   : {result['risk_score']:.2f} / 1.00")
    print(f"  Verdict      : {headline}")
    print()
    print(f"  {advice}")
    print()
    print("  Why:")
    if result["reasons"]:
        for r in result["reasons"]:
            print(f"   • {r}")
    else:
        print("   • No individual risk signal tripped.")
    print()
    print("  Signals read:")
    print(f"   • premium vs mid     : {sig['premium_delta'] * 100:+.2f}%")
    print(f"   • orders/day (30d)   : {sig['daily_velocity']:.1f}"
          f"  ({'new account' if sig['is_new_account'] else 'aged account'})")
    print(f"   • completion rate    : {sig['completion_rate_pct']:.1f}%")
    print()
    if result["category"] == "CLEAN":
        print("  Note: 'likely safe' is not 'guaranteed'. A patient mule on an")
        print("  aged at-market account can still look clean on public data —")
        print("  always keep the trade inside platform escrow.")
        print()

    # Exit code: 0 clean, 1 suspicious, 3 high-risk — usable in scripts.
    return {"CLEAN": 0, "SUSPICIOUS": 1, "HIGH_RISK": 3}[result["category"]]


if __name__ == "__main__":
    raise SystemExit(main())
