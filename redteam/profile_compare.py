"""Compare the exchange (balanced) vs retail-safe (recall-first) profiles.

The retail user's goal is to catch 漏网之鱼 — the mules that slip through —
even at the cost of some false alarms. This script runs both profiles over the
adversarial case battery AND a harder set of borderline mules, and reports the
recall (mules caught) and the false-alarm rate, so the tradeoff is explicit
rather than assumed.

For a retail self-protection tool, a flagged honest trader costs nothing (skip,
pick another); a missed mule costs a frozen account. So we optimise recall.
"""

from __future__ import annotations

from config_loader import AppConfig
from core.p2p_scorer import P2PTraderMetadata, evaluate_counterparty_risk
from redteam.adversarial_cases import build_cases

MID = 4.20


def _m(prem_pct, age, orders, finish, tid="X"):
    return P2PTraderMetadata(
        trader_id=tid,
        advertised_price_fiat=round(MID * (1 + prem_pct / 100), 6),
        market_mid_price_fiat=MID,
        account_age_days=age,
        total_orders_30d=orders,
        completion_rate_pct=finish,
    )


def harder_mules():
    """Borderline mules that a balanced profile is likely to miss — the
    '漏网之鱼' the retail user is actually worried about."""
    return [
        ("borderline_low_premium_new", _m(1.1, 18, 700, 99.0)),   # 1.1% prem, new, ~39/day
        ("borderline_aged_small_premium", _m(1.3, 250, 2400, 99.5)),  # aged, 1.3% prem
        ("borderline_new_modest_velocity", _m(0.6, 22, 620, 99.8)),  # new, ~28/day, tiny prem
        ("borderline_premium_only", _m(1.4, 300, 1500, 99.9)),    # aged, 1.4% prem only
        ("borderline_new_slightlyfast", _m(0.4, 15, 480, 99.0)),  # new, ~32/day, near-mid
    ]


def evaluate_profile(name, cfg):
    cases = build_cases()
    honest = [(c.name, c.meta) for c in cases if c.intent == "HONEST"]
    mules = [(c.name, c.meta) for c in cases if c.intent == "MULE"]
    mules += harder_mules()

    caught = sum(
        1 for _, meta in mules
        if evaluate_counterparty_risk(meta, cfg)["category"] != "CLEAN"
    )
    false_alarms = sum(
        1 for _, meta in honest
        if evaluate_counterparty_risk(meta, cfg)["category"] != "CLEAN"
    )
    recall = caught / len(mules)
    fa_rate = false_alarms / len(honest)

    print(f"--- {name}")
    print(f"    mules caught (recall) : {caught}/{len(mules)}  = {recall:.0%}")
    print(f"    false alarms          : {false_alarms}/{len(honest)} = {fa_rate:.0%}")
    missed = [n for n, meta in mules
              if evaluate_counterparty_risk(meta, cfg)["category"] == "CLEAN"]
    if missed:
        print(f"    STILL MISSED          : {', '.join(missed)}")
    print()
    return recall, fa_rate


def main():
    print("=" * 64)
    print("PROFILE COMPARISON — catching 漏网之鱼 (the ones that slip through)")
    print("=" * 64)
    print()
    r_ex, fa_ex = evaluate_profile("EXCHANGE (balanced, precision-first)", AppConfig())
    r_rs, fa_rs = evaluate_profile("RETAIL-SAFE (recall-first)", AppConfig.retail_safe())

    print("INTERPRETATION")
    print(f"  Retail-safe lifts mule recall {r_ex:.0%} -> {r_rs:.0%}, at the cost")
    print(f"  of false alarms {fa_ex:.0%} -> {fa_rs:.0%}. For self-protection that")
    print("  is the right trade: a false alarm = skip one trader; a missed mule")
    print("  = frozen bank account. Whatever stays in 'STILL MISSED' is the")
    print("  at-market aged mule the public data genuinely cannot separate.")


if __name__ == "__main__":
    main()
