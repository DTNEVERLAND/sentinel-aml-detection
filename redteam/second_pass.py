"""Second-pass self-review: attack the *fixed* model for deeper flaws.

Round 1 (adversarial_cases.py) found and closed two holes. A single red-team
pass proves nothing durable, so this pass hunts three further failure classes:

  A. Monotonicity gaps — any region where making a trader MORE mule-like
     (higher premium, higher velocity, lower completion) LOWERS the score.
     Such a gap is directly exploitable: a mule tunes into the dip.
  B. Type-confusion at the validation boundary — bool-as-int, NaN, etc.
  C. The residual evasion the fix did NOT close: an at-market, aged, modest-
     velocity mule. We quantify exactly how much laundering volume can hide
     under CLEAN, so it can be documented honestly rather than hidden.
"""

from __future__ import annotations

from config_loader import AppConfig
from core.p2p_scorer import P2PTraderMetadata, evaluate_counterparty_risk

CFG = AppConfig()
MID = 4.20


def _score(premium_pct=0.0, age=300, orders=0, finish=99.0):
    return evaluate_counterparty_risk(
        P2PTraderMetadata(
            trader_id="X",
            advertised_price_fiat=round(MID * (1 + premium_pct / 100), 6),
            market_mid_price_fiat=MID,
            account_age_days=age,
            total_orders_30d=orders,
            completion_rate_pct=finish,
        ),
        CFG,
    )["risk_score"]


def check_monotonicity():
    print("A. MONOTONICITY (more mule-like must never lower the score)")
    failures = 0

    # Premium sweep (new account so velocity is fixed-zero at low orders)
    prev = -1.0
    for p in [x / 10 for x in range(0, 120)]:  # 0.0% .. 12.0%
        s = _score(premium_pct=p, age=400, orders=0, finish=99.0)
        if s < prev - 1e-9:
            print(f"   PREMIUM non-monotonic at {p:.1f}%: {s} < {prev}")
            failures += 1
        prev = s

    # Velocity sweep on a new account
    prev = -1.0
    for o in range(0, 6000, 25):
        s = _score(premium_pct=0.0, age=10, orders=o, finish=99.0)
        if s < prev - 1e-9:
            print(f"   VELOCITY non-monotonic at {o} orders: {s} < {prev}")
            failures += 1
        prev = s

    # Completion sweep (lower finish must not lower risk)
    prev = -1.0
    for f in [x / 2 for x in range(200, -1, -1)]:  # 100.0 .. 0.0
        s = _score(premium_pct=0.0, age=10, orders=600, finish=f)
        if s < prev - 1e-9:
            print(f"   COMPLETION non-monotonic at finish={f}: {s} < {prev}")
            failures += 1
        prev = s

    print(f"   -> {'PASS' if failures == 0 else str(failures) + ' GAPS'}\n")
    return failures


def check_type_confusion():
    print("B. TYPE CONFUSION at the validation boundary")
    failures = 0
    bad_inputs = [
        ("account_age_days=True (bool)", dict(account_age_days=True)),
        ("total_orders_30d=False (bool)", dict(total_orders_30d=False)),
        ("account_age_days=30.0 (float)", dict(account_age_days=30.0)),
        ("premium=NaN via price", dict(advertised_price_fiat=float("nan"))),
    ]
    for label, override in bad_inputs:
        base = dict(
            trader_id="X",
            advertised_price_fiat=MID,
            market_mid_price_fiat=MID,
            account_age_days=100,
            total_orders_30d=10,
            completion_rate_pct=99.0,
        )
        base.update(override)
        try:
            evaluate_counterparty_risk(P2PTraderMetadata(**base), CFG)
            print(f"   ACCEPTED bad input: {label}  <<< should have raised")
            failures += 1
        except ValueError:
            pass  # correct
    print(f"   -> {'PASS' if failures == 0 else str(failures) + ' LEAKS'}\n")
    return failures


def quantify_residual_evasion():
    print("C. RESIDUAL EVASION (at-market, aged, modest velocity — the known gap)")
    # Walk an aged mule up the velocity ladder at market price, 100% finish,
    # and find the highest orders/day that still scores CLEAN.
    susp = CFG.p2p_suspicious_threshold
    best_clean_orders = 0
    for o in range(0, 9000, 30):
        s = _score(premium_pct=0.0, age=365, orders=o, finish=100.0)
        if s < susp:
            best_clean_orders = o
    per_day = best_clean_orders / 30
    print(f"   An aged, at-market, perfect-finish account can run up to")
    print(f"   ~{per_day:.0f} orders/day and still score CLEAN.")
    print(f"   This is the documented limit of snapshot-only data: with no")
    print(f"   premium and no account-age anomaly, nothing in the public")
    print(f"   listing separates this mule from a real merchant. Closing it")
    print(f"   needs cross-account / bank-linkage data the tool cannot see.\n")
    return 0  # documented, not a regression


def main():
    total = 0
    total += check_monotonicity()
    total += check_type_confusion()
    quantify_residual_evasion()
    print("=" * 60)
    print(f"SECOND-PASS RESULT: {'CLEAN' if total == 0 else str(total) + ' ISSUES'}")
    return total


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
