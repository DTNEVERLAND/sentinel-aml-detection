# Design Decisions

Every threshold in Sentinel is a deliberate trade-off between false-positive cost and false-negative cost. This doc explains each choice.

## Why >95% withdrawal ratio?

The withdrawal-to-deposit value ratio is the strongest single signal that a user deposited specifically to cash out — not to trade.

- **Below 80%**: too noisy. A trader who sells most of a position to take profit might leave ~75% in fiat while keeping a small altcoin position. False positives spike here.
- **80–95%**: the ambiguous zone. Some real traders cash out 90% of a position. Flagging here trades precision for recall.
- **>95%**: near-total drain. At this ratio, the user kept essentially zero altcoin — the deposit was a vehicle, not an asset. False positives drop sharply.
- **100% (exact)**: too narrow. Real layerers rarely hit exactly 100% because of fee deductions, slippage, and price drift between conversion and withdrawal.

The 95% threshold sits at the inflection point where precision becomes high enough that auto-mitigation is justified.

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

## What this design deliberately doesn't do

- **No automatic SAR filing**. SAR drafts are auditor-bait — they need human compliance officer judgment. Sentinel only flags and holds; the SAR decision is downstream.
- **No customer-facing messaging**. The user sees their withdrawal status as "under review." They don't see the rule name, threshold, or evidence. Telling launderers exactly which rule fired teaches them how to evade.
- **No cross-rule correlation in this version**. Sentinel is one rule, doing one job well. Combining signals across rules (e.g. layering + sanctions hit + first-time withdrawal) is a v2 capability that requires the v1 to be running cleanly first.
