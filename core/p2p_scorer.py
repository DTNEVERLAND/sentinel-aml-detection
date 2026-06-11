"""P2PBehavioralScorer — counterparty risk scoring for MYR P2P crypto trades.

Context
-------
In the Malaysian P2P market, mule syndicates flush scam proceeds by posting
buy ads at a premium over market mid: the premium buys queue position and
speed. A retail seller who matches with one receives tainted MYR, and when a
scam victim reports the chain, the seller's bank account is frozen. Every
signal that distinguishes a mule from a genuine buyer — ad premium, order
velocity, account age, completion rate — is publicly visible on the listing,
but no tool synthesises them into a pre-trade decision. This module is that
synthesis.

Scoring model
-------------
Three components, each normalised to [0, 1]:

1. Premium  — ramps from 0 at ``p2p_max_allowed_premium`` to 1 at three times
   that premium. Strongest signal: the premium is the mule's customer-
   acquisition cost and has no honest explanation at scale.
2. Velocity — daily order velocity over the 30-day window. The window divisor
   is ``min(account_age_days, 30)``: the order count only covers 30 days, so
   dividing by the full account age would systematically understate velocity
   for older accounts. Two red-team-driven design choices (see redteam/):
     · The penalty ramps from *half* the threshold, not from the threshold
       itself, so a mule cannot sit at 49/day to evade a 50/day cliff.
     · New accounts (< ``p2p_new_account_age_days``) are penalised at full
       weight; older accounts at only a 0.15 discount. Real Binance P2P data
       shows legitimate market makers run 57–298 orders/day, so high absolute
       volume on an aged account is NOT a mule signal — premium and completion
       carry the load there. The discount is non-zero only because bought-aged
       accounts are a documented tactic; it is deliberately small.
3. Completion — linear ramp below ``p2p_min_completion_rate``, saturating at
   ``p2p_completion_floor_pct``. Deliberately weak: industrial mule accounts
   often have *high* completion rates, so a low rate alone marks a flaky
   counterparty, not a launderer.

Components combine noisy-OR style::

    risk = 1 - (1 - 0.82·premium)(1 - 0.72·velocity)(1 - 0.35·completion)

so one decisive signal escalates on its own (a maxed premium or velocity
crosses the HIGH_RISK band without help), independent corroborating signals
compound, and the score is bounded in [0, 1) by construction.

The scorer is stateless and deterministic: no globals, no I/O, no clock, no
network. Same metadata + same config ⇒ identical output, always.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from config_loader import AppConfig

CATEGORY_CLEAN = "CLEAN"
CATEGORY_SUSPICIOUS = "SUSPICIOUS"
CATEGORY_HIGH_RISK = "HIGH_RISK"

# Evidence weights for the noisy-OR combination. Premium is the strongest
# single signal, completion the weakest (see module docstring). Premium is
# calibrated so that double the allowed premium (half-ramp) lands SUSPICIOUS:
# 0.82 × 0.5 = 0.41 ≥ 0.40 band.
_W_PREMIUM = 0.82
_W_VELOCITY = 0.72
_W_COMPLETION = 0.35

# Velocity penalty discount for accounts older than the "new" cutoff.
# Small but non-zero: real market makers run 57-298 orders/day legitimately
# (Binance P2P data), so aged high volume is mostly noise — but bought aged
# accounts are a real tactic, so the signal never fully zeroes.
_AGED_VELOCITY_DISCOUNT = 0.15

# Velocity ramp starts at this fraction of the threshold, so a mule cannot
# park just under the line (49/day vs a 50/day cliff) to score zero. At
# 0.5·threshold the penalty is 0; at 1.5·threshold it saturates.
_VELOCITY_RAMP_FLOOR_FRAC = 0.5

# Premium component saturates at threshold + ramp, where ramp is this
# multiple of the threshold (default 1.5% → saturation at 4.5% premium).
_PREMIUM_RAMP_MULTIPLE = 2.0

# The marketplace order statistic covers a fixed trailing window.
_ORDER_WINDOW_DAYS = 30


@dataclass(frozen=True)
class P2PTraderMetadata:
    """Publicly visible listing data for one P2P counterparty."""

    trader_id: str
    advertised_price_fiat: float
    market_mid_price_fiat: float
    account_age_days: int
    total_orders_30d: int
    completion_rate_pct: float


def _require_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


def _require_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")


def _validate_metadata(metadata: P2PTraderMetadata) -> None:
    """Fail fast on corrupt input. Raises ValueError with a specific message."""
    if not isinstance(metadata.trader_id, str) or not metadata.trader_id.strip():
        raise ValueError("trader_id must be a non-empty string")

    _require_finite("advertised_price_fiat", metadata.advertised_price_fiat)
    if metadata.advertised_price_fiat <= 0:
        raise ValueError(
            f"advertised_price_fiat must be > 0, got {metadata.advertised_price_fiat!r}"
        )

    _require_finite("market_mid_price_fiat", metadata.market_mid_price_fiat)
    if metadata.market_mid_price_fiat <= 0:
        raise ValueError(
            f"market_mid_price_fiat must be > 0, got {metadata.market_mid_price_fiat!r}"
        )

    _require_int("account_age_days", metadata.account_age_days)
    if metadata.account_age_days <= 0:
        raise ValueError(
            f"account_age_days must be > 0, got {metadata.account_age_days!r}"
        )

    _require_int("total_orders_30d", metadata.total_orders_30d)
    if metadata.total_orders_30d < 0:
        raise ValueError(
            f"total_orders_30d must be >= 0, got {metadata.total_orders_30d!r}"
        )

    _require_finite("completion_rate_pct", metadata.completion_rate_pct)
    if not 0.0 <= metadata.completion_rate_pct <= 100.0:
        raise ValueError(
            f"completion_rate_pct must be in [0, 100], got {metadata.completion_rate_pct!r}"
        )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class P2PBehavioralScorer:
    """Stateless scorer. Safe to share; every call is independent."""

    def evaluate_counterparty_risk(
        self, metadata: P2PTraderMetadata, config: AppConfig
    ) -> dict:
        """Score one counterparty. Returns a dict with score, category,
        per-component breakdown, raw signals, and human-readable reasons.
        """
        _validate_metadata(metadata)

        reasons: list[str] = []

        # ── 1. Laundering premium ────────────────────────────────────────
        premium_delta = (
            metadata.advertised_price_fiat - metadata.market_mid_price_fiat
        ) / metadata.market_mid_price_fiat
        ramp = _PREMIUM_RAMP_MULTIPLE * config.p2p_max_allowed_premium
        premium_component = _clamp01(
            (premium_delta - config.p2p_max_allowed_premium) / ramp
        )
        if premium_component > 0:
            reasons.append(
                f"Buy ad priced {premium_delta:.2%} above market mid "
                f"(allowed {config.p2p_max_allowed_premium:.2%}) — premium "
                "buying is the classic dirty-fiat flush pattern"
            )

        # ── 2. Order velocity ────────────────────────────────────────────
        # Order count covers a 30-day window, so the divisor is capped at 30:
        # dividing by full account age understates velocity for old accounts.
        effective_days = min(metadata.account_age_days, _ORDER_WINDOW_DAYS)
        daily_velocity = metadata.total_orders_30d / max(effective_days, 1)
        # Ramp from half the threshold to 1.5x the threshold (soft shoulder),
        # so parking just under the line still accrues partial risk.
        ramp_floor = _VELOCITY_RAMP_FLOOR_FRAC * config.p2p_velocity_threshold
        raw_velocity_component = _clamp01(
            (daily_velocity - ramp_floor) / config.p2p_velocity_threshold
        )
        is_new_account = metadata.account_age_days < config.p2p_new_account_age_days
        if is_new_account:
            velocity_component = raw_velocity_component
        else:
            velocity_component = _AGED_VELOCITY_DISCOUNT * raw_velocity_component
        if velocity_component > 0:
            age_note = (
                f"{metadata.account_age_days}-day-old account"
                if is_new_account
                else f"aged account ({metadata.account_age_days} days; discounted, "
                "but bought aged accounts are a known mule tactic)"
            )
            reasons.append(
                f"{daily_velocity:.1f} orders/day against a threshold of "
                f"{config.p2p_velocity_threshold:g} — industrial throughput on a "
                f"{age_note}"
            )

        # ── 3. Completion trust ──────────────────────────────────────────
        span = config.p2p_min_completion_rate - config.p2p_completion_floor_pct
        completion_component = _clamp01(
            (config.p2p_min_completion_rate - metadata.completion_rate_pct) / span
        )
        if completion_component > 0:
            reasons.append(
                f"Completion rate {metadata.completion_rate_pct:.1f}% below the "
                f"{config.p2p_min_completion_rate:g}% trust floor — weak signal "
                "on its own, compounds with others"
            )

        # ── Noisy-OR combination ─────────────────────────────────────────
        risk_score = 1.0 - (
            (1.0 - _W_PREMIUM * premium_component)
            * (1.0 - _W_VELOCITY * velocity_component)
            * (1.0 - _W_COMPLETION * completion_component)
        )
        risk_score = _clamp01(risk_score)

        if risk_score >= config.p2p_high_risk_threshold:
            category = CATEGORY_HIGH_RISK
        elif risk_score >= config.p2p_suspicious_threshold:
            category = CATEGORY_SUSPICIOUS
        else:
            category = CATEGORY_CLEAN

        return {
            "trader_id": metadata.trader_id,
            "risk_score": round(risk_score, 4),
            "category": category,
            "components": {
                "premium": round(premium_component, 4),
                "velocity": round(velocity_component, 4),
                "completion": round(completion_component, 4),
            },
            "signals": {
                "premium_delta": round(premium_delta, 6),
                "daily_velocity": round(daily_velocity, 4),
                "effective_window_days": effective_days,
                "is_new_account": is_new_account,
                "completion_rate_pct": metadata.completion_rate_pct,
            },
            "reasons": reasons,
        }


def evaluate_counterparty_risk(metadata: P2PTraderMetadata, config: AppConfig) -> dict:
    """Module-level convenience wrapper around P2PBehavioralScorer."""
    return P2PBehavioralScorer().evaluate_counterparty_risk(metadata, config)


# Backwards-compatible alias from the v1 API.
evaluate_p2p_risk = evaluate_counterparty_risk
