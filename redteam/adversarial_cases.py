"""Adversarial red-team harness for P2PBehavioralScorer.

This is NOT a unit-test file. Unit tests check that code matches the assertions
I wrote — circular. This harness attacks the *model* with case studies built
from two sources:

  1. Real Binance P2P merchant-stat distributions (redteam/data/idr_*.json),
     so "normal" is grounded in what genuine market makers actually look like.
  2. Hand-crafted evasion profiles — mules who deliberately shape their public
     stats to slip under each heuristic.

It prints a confusion matrix (intended label vs. scorer category) and lists
every FALSE NEGATIVE (mule scored CLEAN) and FALSE POSITIVE (honest trader
scored SUSPICIOUS/HIGH_RISK). Those two lists are the vulnerability report.
"""

from __future__ import annotations

import json
import os
import statistics as st
from dataclasses import dataclass

from config_loader import AppConfig
from core.p2p_scorer import P2PTraderMetadata, evaluate_counterparty_risk

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@dataclass
class Case:
    name: str
    intent: str  # "HONEST" or "MULE"
    meta: P2PTraderMetadata


def _real_distributions() -> dict:
    """Pull premium / velocity / finish-rate distributions from the snapshot."""
    def load(side):
        with open(os.path.join(DATA_DIR, f"idr_{side}.json"), encoding="utf-8") as f:
            rows = json.load(f)["data"]
        recs = []
        for r in rows:
            adv, a = r["adv"], r["advertiser"]
            recs.append(
                {
                    "price": float(adv["price"]),
                    "orders": a.get("monthOrderCount") or 0,
                    "finish": (a.get("monthFinishRate") or 0) * 100,
                }
            )
        return recs

    sell, buy = load("SELL"), load("BUY")
    mid = (sell[0]["price"] + buy[0]["price"]) / 2
    allrecs = sell + buy
    return {
        "mid": mid,
        "orders_med": st.median(r["orders"] for r in allrecs),
        "orders_max": max(r["orders"] for r in allrecs),
        "finish_med": st.median(r["finish"] for r in allrecs),
        "premium_max_pct": max((r["price"] - mid) / mid * 100 for r in allrecs),
    }


def build_cases() -> list[Case]:
    d = _real_distributions()
    mid = 4.20  # use a clean MYR-scale mid; ratios are what matter
    cases: list[Case] = []

    def m(tid, prem_pct, age, orders30, finish):
        return P2PTraderMetadata(
            trader_id=tid,
            advertised_price_fiat=round(mid * (1 + prem_pct / 100), 6),
            market_mid_price_fiat=mid,
            account_age_days=age,
            total_orders_30d=orders30,
            completion_rate_pct=finish,
        )

    # ── HONEST traders grounded in real distributions ───────────────────────
    cases += [
        Case("honest_median_merchant", "HONEST",
             m("H1", -0.18, 540, int(d["orders_med"]), d["finish_med"])),
        Case("honest_top_volume_maker", "HONEST",
             m("H2", 0.05, 800, int(d["orders_max"]), 99.5)),  # 298/day, legit
        Case("honest_small_retailer", "HONEST",
             m("H3", 0.0, 120, 45, 99.0)),
        Case("honest_new_but_slow", "HONEST",
             m("H4", 0.10, 8, 60, 98.0)),  # new, 7.5/day — ramping normally
        Case("honest_flaky_parttimer", "HONEST",
             m("H5", 0.2, 200, 90, 91.0)),  # low-ish finish, low volume
        Case("honest_fx_volatility_buyer", "HONEST",
             m("H6", 0.9, 400, 200, 99.0)),  # 0.9% premium during FX spike
    ]

    # ── MULE profiles (adversarial) ─────────────────────────────────────────
    cases += [
        Case("mule_classic_premium_runner", "MULE",
             m("M1", 5.9, 6, 720, 97.0)),         # textbook: premium+new+fast
        Case("mule_at_market_stealth", "MULE",
             m("M2", 0.0, 5, 600, 98.5)),         # NO premium — evades signal 1
        Case("mule_bought_aged_account", "MULE",
             m("M3", 2.5, 400, 4500, 99.0)),      # aged shell, sudden 150/day
        Case("mule_slow_burn_premium", "MULE",
             m("M4", 3.0, 9, 280, 99.0)),         # premium + new + ~31/day
        Case("mule_perfect_stats_premium", "MULE",
             m("M5", 4.0, 12, 400, 100.0)),       # 100% finish to look elite
        Case("mule_just_under_velocity", "MULE",
             m("M6", 2.0, 20, 980, 99.0)),        # 49/day — gaming the 50 line
        Case("mule_low_premium_high_velocity", "MULE",
             m("M7", 0.8, 7, 700, 99.0)),         # premium under 1.5%, fast+new
    ]
    return cases


def main():
    cfg = AppConfig()
    cases = build_cases()

    labels = ("CLEAN", "SUSPICIOUS", "HIGH_RISK")
    # For scoring: HONEST should be CLEAN; MULE should be SUSPICIOUS or HIGH_RISK.
    false_neg, false_pos, rows = [], [], []
    confusion = {"HONEST": {l: 0 for l in labels}, "MULE": {l: 0 for l in labels}}

    for c in cases:
        res = evaluate_counterparty_risk(c.meta, cfg)
        cat = res["category"]
        confusion[c.intent][cat] += 1
        rows.append((c.name, c.intent, cat, res["risk_score"]))
        if c.intent == "MULE" and cat == "CLEAN":
            false_neg.append((c.name, res))
        if c.intent == "HONEST" and cat != "CLEAN":
            false_pos.append((c.name, res))

    print("=" * 70)
    print("ADVERSARIAL RESULTS  (intent -> scored category)")
    print("=" * 70)
    for name, intent, cat, score in rows:
        flag = ""
        if intent == "MULE" and cat == "CLEAN":
            flag = "  <<< FALSE NEGATIVE (mule slipped through)"
        if intent == "HONEST" and cat != "CLEAN":
            flag = "  <<< FALSE POSITIVE (honest flagged)"
        print(f"  {intent:6} {score:5.2f} {cat:11} {name}{flag}")

    print("\nCONFUSION MATRIX")
    print(f"  {'':8}{'CLEAN':>11}{'SUSPICIOUS':>12}{'HIGH_RISK':>11}")
    for intent in ("HONEST", "MULE"):
        r = confusion[intent]
        print(f"  {intent:8}{r['CLEAN']:>11}{r['SUSPICIOUS']:>12}{r['HIGH_RISK']:>11}")

    n_mule = sum(1 for c in cases if c.intent == "MULE")
    n_honest = sum(1 for c in cases if c.intent == "HONEST")
    print(f"\n  FALSE NEGATIVES: {len(false_neg)}/{n_mule} mules scored CLEAN")
    print(f"  FALSE POSITIVES: {len(false_pos)}/{n_honest} honest scored non-CLEAN")

    if false_neg:
        print("\n--- FALSE NEGATIVES (mules the model missed) ---")
        for name, res in false_neg:
            print(f"  {name}: score={res['risk_score']} comps={res['components']}")
    if false_pos:
        print("\n--- FALSE POSITIVES (honest traders wrongly flagged) ---")
        for name, res in false_pos:
            print(f"  {name}: score={res['risk_score']} comps={res['components']}")

    return len(false_neg), len(false_pos)


if __name__ == "__main__":
    fn, fp = main()
    raise SystemExit(1 if (fn or fp) else 0)
