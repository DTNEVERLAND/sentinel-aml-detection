# The Problem: Pass-Through Layering

## What is pass-through layering?

A money launderer deposits crypto into an exchange, **immediately** converts it to fiat, and withdraws to a bank account — typically all within minutes. The "deposit → convert → withdraw" chain happens fast enough that:

- The exchange's threshold-based alerts haven't fired yet (volume per leg is small).
- By the time an analyst reviews, the fiat is already in a downstream bank.
- Recovery requires cross-border MLATs, frozen-account requests, and weeks of work — most of which never recovers the funds.

This is one of the most common typologies referenced in FATF VASP guidance, Chainalysis's annual Crypto Crime Report, and exchange-side AML postmortems. It's also one of the hardest to catch with standard rule sets, because each individual leg looks normal.

## Why threshold-based monitoring misses it

Most exchange AML stacks are built on three primitives:

| Rule type | What it catches | What it misses |
|---|---|---|
| **Volume thresholds** | Large single transfers (e.g. >$10k) | Layered transfers below the threshold |
| **Velocity rules** | Many transactions in a short window | Single high-value pass-throughs |
| **Address screening** | Known bad addresses (OFAC SDN, Chainalysis-flagged) | First-use addresses, fresh wallets |

Pass-through layering deliberately operates **under** these rules. The launderer sizes each leg below volume thresholds, doesn't repeat the pattern enough to trip velocity rules, and uses clean addresses. From the exchange's perspective, every individual transaction looks fine. The pattern only emerges when you **pair** specific deposits with specific withdrawals.

## The specific signature Sentinel targets

Three signals, AND-ed together:

1. **Volatile altcoin deposit** — ADA, XRP, or similar low-friction high-volume tokens. Stablecoin deposits are excluded because they have different (and well-monitored) typologies.
2. **Fast fiat conversion** — Withdrawal request placed within 120 minutes of the deposit clearing.
3. **Near-total drain** — Withdrawal value lands at 95–105% of everything deposited in the prior two hours. A band, not a floor: below it, the user kept a real position (probably a trader); far above it, the withdrawal is just pre-existing balance leaving that happens to coincide with a small deposit.

Any one of these alone is benign. Together, they describe a user who deposited specifically to convert and exit — not to trade. That's the layering fingerprint.

## Why this specific tripwire matters

The 2-hour window matters because **fiat-rail clearing windows** are usually 1–3 business days at most APAC exchanges. A 72-hour cooldown after detection means the hold sits *across* the clearing window — giving compliance time to review while the legitimate user only experiences a 3-day inconvenience, not a permanent freeze.

This is the central design choice: **reversibility over speed**. The cost of an unwarranted hold is a customer-service ticket; the cost of letting layered funds settle is regulatory exposure plus the entire withdrawn amount.
