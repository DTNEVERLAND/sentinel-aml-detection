# Module 2 — P2P Counterparty Risk Scorer

> *Check the buyer before the bank checks you.*

The user-side half of Sentinel. Where the [layering detector](./architecture.md) is exchange-side and catches launderers in flight, this module protects the **retail seller** from *becoming the next hop in the chain* — scoring a counterparty's publicly visible P2P listing data before the user sells and dirty fiat lands in their bank account.

## The problem in one paragraph

Mule syndicates flush scam proceeds through P2P markets by posting buy ads **above market price**: the premium buys queue position and speed. A retail seller who matches with one receives tainted fiat; when a scam victim reports the chain, the bank freezes every downstream account — including the seller's. The seller did nothing wrong and loses banking access for weeks. Every signal that distinguishes a mule from a genuine buyer is visible on the listing page, but nothing synthesises them into a decision at the moment that matters: **before you click sell**.

## How it scores

Three signals from the public listing, each normalised to [0, 1], combined noisy-OR style so one decisive signal escalates on its own and corroborating signals compound:

| Signal | Why it works | Weight |
|---|---|---|
| **Laundering premium** — buy ad priced above market mid | The premium is the mule's customer-acquisition cost. Real Binance P2P data shows legitimate ads sit within ~0.12% of mid, so the 1.0% threshold is already a strong anomaly | 0.82 |
| **Order velocity** — orders/day, account-age-aware | Absolute volume can't separate a mule from a pro merchant (both run hundreds/day). The signal is high velocity *on a new account*; aged accounts are nearly exempt | 0.72 |
| **Completion decay** — completion rate below 95% | Weak on purpose: mule accounts often have *high* completion. Alone it can never cross SUSPICIOUS; it only compounds | 0.35 |

```
risk = 1 − (1 − 0.82·premium)(1 − 0.72·velocity)(1 − 0.35·completion)
```

Bands (configurable): `≥ 0.70 → HIGH_RISK` · `≥ 0.40 → SUSPICIOUS` · else `CLEAN`.

## Calibration: every threshold is data-driven, not guessed

The original spec proposed `premium > 1.5%`, `velocity > 50/day absolute`, and a hard `age < 30` gate. Pulling real Binance P2P merchant stats (`redteam/data/`) showed all three were wrong, and red-teaming the model proved it:

1. **Premium threshold 1.5% → 1.0%.** Legitimate ads sit within 0.12% of mid; 1.5% was far too loose to catch a premium-paying mule. 1.0% keeps headroom for FX spikes.
2. **Velocity is account-age-aware, not absolute.** Real merchants run 57–298 orders/day legitimately, so a flat 50/day threshold flags every pro merchant (false positive) *and* lets a mule park at 49/day (false negative). New accounts get full velocity weight; aged accounts only a 0.15 discount.
3. **The velocity divisor is capped at 30 days,** because the order count only spans 30 days — dividing by full account age understates old accounts 10×.
4. **Soft shoulder on velocity.** The penalty ramps from *half* the threshold, so a mule can't sit one order under a cliff to score zero.

## Red-teaming: attacking my own scorer

Unit tests only prove the code matches assertions I wrote — circular. The real validation is in `redteam/`, which attacks the *model* with case studies built from real merchant distributions plus hand-crafted evasion profiles.

**Round 1** (`adversarial_cases.py`) — 13 profiles (6 honest, 7 mule). The first run exposed two holes, both rooted in the absolute velocity threshold:
- *False positive:* a 298-orders/day legitimate market maker scored SUSPICIOUS.
- *False negative:* a mule parked at 49/day scored CLEAN.

After the calibration fixes: **0 false negatives, 0 false positives, 13/13 correctly classified.**

**Round 2** (`second_pass.py`) — deeper failure classes:
- *Monotonicity:* swept premium/velocity/completion across thousands of points — no exploitable dip where making a trader more mule-like lowers its score. **PASS.**
- *Type confusion:* bool-as-int, float age, NaN price all rejected at the boundary. **PASS.**
- *Residual evasion (honestly documented):* an at-market, aged, perfect-completion account can run up to **~299 orders/day and still score CLEAN** — almost exactly the real legitimate maximum (298/day). That's not a bug to patch; it's the hard limit of snapshot-only public data. With no premium and no age anomaly, *nothing in the public listing separates this mule from a real merchant.* Closing it requires cross-account / bank-linkage data only the exchange has.

```
PYTHONPATH=. py redteam/adversarial_cases.py   # confusion matrix, exits 1 on any miss
PYTHONPATH=. py redteam/second_pass.py         # monotonicity + type + residual gap
```

## Two profiles, because the cost of being wrong is asymmetric

| | False positive | False negative | Tune for |
|---|---|---|---|
| **Retail-safe** (default) | Skip one trader — 30s lost | **Frozen bank account** | **Recall** |
| **Exchange** (`--exchange`) | Annoy a real customer | One missed flag among many | Precision |

For a retail seller the two errors are wildly unequal, so the default profile leans hard toward catching the mules that slip through. Measured on the case battery (`redteam/profile_compare.py`):

```
EXCHANGE     mule recall  58%   false alarms 0%
RETAIL-SAFE  mule recall 100%   false alarms 0%   ← default
```

Retail-safe also passes all 40 **real Binance P2P merchants** in the snapshot as CLEAN — it raises recall without turning genuine traders into noise.

## Use it before a trade (CLI)

`check_trader.py` is what a retail seller actually runs. Feed it the numbers on the buyer's listing; it gives a plain verdict.

```
py check_trader.py --premium 2.4 --age 8 --orders30 600 --finish 99
```
```
  Risk score   : 0.95 / 1.00
  Verdict      : ⛔ DO NOT TRADE

  Strong match for a dirty-fiat flush pattern. Trading here risks your bank
  account being frozen when the chain is reported. Walk away.
```

Exit code is `0` clean / `1` caution / `3` do-not-trade, so it scripts cleanly. Pass `--ad-price`/`--mid` instead of `--premium` if you have raw prices.

## Library usage

```python
from config_loader import AppConfig
from core.p2p_scorer import P2PTraderMetadata, evaluate_counterparty_risk

result = evaluate_counterparty_risk(
    P2PTraderMetadata("buyer-001", 4.45, 4.20, 6, 720, 97.0),
    AppConfig.retail_safe(),   # or AppConfig() for exchange-side
)
# {'risk_score': 0.95, 'category': 'HIGH_RISK', 'reasons': [...], ...}
```

Stateless, deterministic, stdlib-only. No network, no clock, no globals: same input ⇒ same output, always. `py -m pytest tests/` → 48 tests.

## Known evasions (and why it ships anyway)

| Evasion | Why it's missed | Counter |
|---|---|---|
| **At-market aged mule** — aged account, post at mid, stay ≤~299 orders/day | Quantified in `second_pass.py`: no premium + no age anomaly = indistinguishable from a real merchant on public data | Needs cross-account / bank-linkage data only the exchange holds — the documented hard ceiling of a user-side tool |
| **Distributed low-velocity fleets** — many accounts, few orders each | Each account looks retail-scale | Cross-account clustering (device / bank-account linkage) |
| **Stats spoofing** — wash-trade to age stats organically | All public stats look clean | Cost asymmetry: aging an account takes months, freezes burn accounts weekly |
| **Off-listing negotiation** — move the deal to Telegram | No listing to score | Out of scope by definition; the tool's advice *is* the mitigation: don't leave the platform |

A rule whose evasions you can't articulate is a rule you don't understand. The at-market aged mule is the one this tool genuinely cannot catch — the harness quantifies exactly how much volume hides there (~299 orders/day) rather than pretending it's closed.
