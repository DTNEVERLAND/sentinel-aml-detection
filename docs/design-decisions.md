# Design Decisions

Every threshold in Sentinel is a deliberate trade-off between false-positive cost and false-negative cost. This doc explains each choice.

## Why a 95–105% ratio band (not a simple >95% floor)?

The withdrawal-to-deposit value ratio is the strongest single signal that a user deposited specifically to cash out — not to trade. But it only means that if it's a **band**, not a floor.

**The lower bound (95%)** separates layerers from traders:

- **Below 80%**: too noisy. A trader who sells most of a position to take profit might leave ~25% in altcoin. False positives spike here.
- **80–95%**: the ambiguous zone. Some real traders cash out 90% of a position. Flagging here trades precision for recall.
- **>95%**: near-total drain. The user kept essentially zero altcoin — the deposit was a vehicle, not an asset.

**The upper bound (105%) is just as important.** The ratio is *withdrawal ÷ deposits-in-window*. Without a ceiling, the rule also fires on every user who happens to make a small alt deposit within two hours of a large fiat withdrawal funded by their **existing balance** — a $200 ADA deposit followed by a $50,000 fiat withdrawal is a ratio of 250, which sails past a bare `> 0.95` check. Nothing about that pattern suggests the withdrawal was funded by the deposit; it's a guaranteed false-positive class, and each one is a 72-hour hold on an innocent customer.

Capping at 105% keeps tolerance for fee deductions, slippage, and price drift between conversion and withdrawal (which is why exactly-100% is too narrow), while expressing what the signal actually means: **the deposit and the exit are the same money**.

## Why 2 hours (120 minutes)?

The time delta between deposit and withdrawal request is the second signal. The choice of 2 hours balances three things:

1. **Real-trader behavior**: legitimate traders who deposit and convert usually take longer than 2 hours — they wait for a price target, check the market, place limit orders. Sub-2-hour conversion almost always indicates someone with a predetermined plan.
2. **Layerer behavior**: documented layering schemes from Chainalysis case studies typically complete the deposit-to-fiat-withdrawal leg in 5–45 minutes. The 2-hour ceiling captures the long tail.
3. **Pre-emptive mitigation window**: fiat withdrawals at most APAC exchanges are batched (every 1–6 hours). A 2-hour detection window almost always fires *before* the withdrawal enters the next batch — meaning the hold takes effect before any fiat moves.

## Why a 72-hour fiat hold (not freeze, not delete)?

The mitigation is **reversible** by design. Three reasons:

| Option considered | Cost if user is innocent | Cost if user is laundering |
|---|---|---|
| **Permanent freeze** | Account is frozen for days/weeks during dispute — massive CS escalation, customer churn, regulatory risk if disputed | Funds protected ✓ |
| **Reject withdrawal** | Withdrawal vanishes — user resubmits, attacker now knows the rule | User-side rejection is a leak |
| **72-hour cooldown** *(chosen)* | 3-day inconvenience; user can dispute via L2 compliance; reversible | Hold spans typical fiat clearing window, gives compliance time to investigate |

72 hours specifically: longer than typical fiat clearing (24–48hr), short enough that a false positive resolves within a business week.

## Why pre-emptive mitigation (no human in the loop on the cooldown)?

This is the most opinionated choice in Sentinel. Most AML systems use a **review-first** model: alert → analyst reviews → analyst decides → action. Sentinel inverts this: **action → analyst reviews → analyst can reverse**.

Justification:

- **Speed asymmetry**: layered fiat clears in minutes; analyst review takes 10–60+ minutes during business hours and *days* outside them. The window between detection and review is exactly when laundered funds escape.
- **Reversibility makes pre-emptive safe**: if you can undo the hold cleanly, the worst case is a 3-day customer inconvenience. That's manageable. Letting laundered fiat clear is not.
- **L1 analyst experience is preserved**: the alert still goes to a human, with full evidence. The human still decides whether to escalate or lift. The only thing automation removed is the *gating role* on the protective action.

This trade-off only works for narrowly-targeted, high-precision rules. Sentinel is narrow on purpose: ADA/XRP → fiat, >95%, <2hr. It's not a general-purpose monitor. A noisy rule applied pre-emptively would generate user-experience disasters; a precise rule applied pre-emptively is just good defense.

## Why Slack + Jira (not one or the other)?

Different surfaces, different jobs.

- **Slack** is for **awareness**: oncall sees the alert immediately, can react, can comment in the thread. It's where the team's attention lives during business hours.
- **Jira** is for **audit trail**: every flagged event becomes a ticket with a permanent record, assignable owner, resolution state, and timestamped action log. This is what auditors and regulators eventually ask for.

Slack without Jira loses auditability. Jira without Slack loses real-time attention. Both, with the same evidence payload, gives compliance ops a record-keeping layer and an attention layer that don't have to be reconciled manually.

## Known evasions (and why v1 ships anyway)

A rule whose evasions you can't articulate is a rule you don't understand. Sentinel v1 is knowingly evadable by:

| Evasion | Why v1 misses it | v2 counter |
|---|---|---|
| **Split withdrawals** — one deposit, three fiat exits of ~33% each | Ratio is computed per withdrawal; each leg sits below the band | Aggregate the withdrawal side over a trailing window — the mirror image of the deposit-side aggregation v1 already does |
| **Smurfing across accounts** — mule accounts each running the pattern at small scale | The rule is single-user by construction | Device-fingerprint / KYC-document / withdrawal-bank-account linkage to cluster related accounts |
| **Slow layering** — park the fiat and withdraw after the 2-hour window | Outside the time window by design | Velocity rules and balance-origin tracing upstream; widening the window here would destroy precision |
| **Asset rotation** — use volatile alts outside the static ADA/XRP list | Asset list is hardcoded | Liquidity-weighted dynamic asset list, refreshed from market data |
| **Pre-positioned conversion** — deposit, convert, let fiat sit, withdraw later | The conversion (trade) leg isn't verified at all | Join the trades table to tie deposit → sell → withdrawal explicitly |

Note what v1 *does* already counter: **split deposits**. Because the detection aggregates all same-user deposits in the 2-hour window before each withdrawal, breaking one deposit into five small ones neither evades the rule nor fires five duplicate alerts.

Shipping a narrow rule with documented evasions beats shipping a broad rule with undocumented false positives. Every evasion above raises the launderer's cost (more accounts, more time exposed on-platform, more KYC surface), and each one maps to a concrete v2 work item rather than a vague "improve detection" backlog entry.

## What this design deliberately doesn't do

- **No automatic SAR filing**. SAR drafts are auditor-bait — they need human compliance officer judgment. Sentinel only flags and holds; the SAR decision is downstream.
- **No customer-facing messaging**. The user sees their withdrawal status as "under review." They don't see the rule name, threshold, or evidence. Telling launderers exactly which rule fired teaches them how to evade.
- **No cross-rule correlation in this version**. Sentinel is one rule, doing one job well. Combining signals across rules (e.g. layering + sanctions hit + first-time withdrawal) is a v2 capability that requires the v1 to be running cleanly first.
