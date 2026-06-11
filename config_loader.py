"""Immutable application configuration for the ChainGuard P2P scorer.

AppConfig is a frozen dataclass: once constructed it cannot be mutated, so a
config instance can be shared across threads and the scorer stays referentially
transparent. Every threshold is validated at construction time — a config that
would make the scorer meaningless (negative premium, inverted risk bands)
fails fast here instead of producing silent garbage scores later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _require_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


@dataclass(frozen=True)
class AppConfig:
    """Frozen scorer configuration.

    Attributes:
        p2p_max_allowed_premium: Buy-ad premium over market mid above which the
            premium penalty starts accruing. Expressed as a fraction
            (0.010 == 1.0%). NOTE: the original spec proposed 0.015, but live
            Binance P2P data shows legitimate buy ads sit within ~0.12% of mid,
            so 1.5% was far too loose to catch a premium-paying mule. Calibrated
            down to 1.0%, which still leaves headroom for FX-volatility spikes
            on honest ads. Tunable per-market.
        p2p_velocity_threshold: Daily order velocity (orders/day over the
            30-day window) above which the velocity penalty starts accruing.
        p2p_min_completion_rate: Completion rate (percent) below which the
            completion-trust penalty starts accruing.
        p2p_completion_floor_pct: Completion rate at (or below) which the
            completion penalty saturates at its maximum.
        p2p_new_account_age_days: Account age below which a trader is treated
            as "new" and velocity is penalised at full weight. Older accounts
            still accrue a discounted velocity penalty (bought aged accounts
            are a documented mule tactic).
        p2p_suspicious_threshold: Risk score at which a trader is categorised
            SUSPICIOUS.
        p2p_high_risk_threshold: Risk score at which a trader is categorised
            HIGH_RISK.
    """

    p2p_max_allowed_premium: float = 0.010
    p2p_velocity_threshold: float = 50.0
    p2p_min_completion_rate: float = 95.0
    p2p_completion_floor_pct: float = 80.0
    p2p_new_account_age_days: int = 30
    p2p_suspicious_threshold: float = 0.40
    p2p_high_risk_threshold: float = 0.70

    def __post_init__(self) -> None:
        _require_finite("p2p_max_allowed_premium", self.p2p_max_allowed_premium)
        if not 0.0 < self.p2p_max_allowed_premium < 1.0:
            raise ValueError(
                "p2p_max_allowed_premium must be in (0, 1) — it is a fraction, "
                f"got {self.p2p_max_allowed_premium!r}"
            )

        _require_finite("p2p_velocity_threshold", self.p2p_velocity_threshold)
        if self.p2p_velocity_threshold <= 0:
            raise ValueError(
                f"p2p_velocity_threshold must be > 0, got {self.p2p_velocity_threshold!r}"
            )

        _require_finite("p2p_min_completion_rate", self.p2p_min_completion_rate)
        if not 0.0 < self.p2p_min_completion_rate <= 100.0:
            raise ValueError(
                "p2p_min_completion_rate must be in (0, 100], "
                f"got {self.p2p_min_completion_rate!r}"
            )

        _require_finite("p2p_completion_floor_pct", self.p2p_completion_floor_pct)
        if not 0.0 <= self.p2p_completion_floor_pct < self.p2p_min_completion_rate:
            raise ValueError(
                "p2p_completion_floor_pct must satisfy "
                "0 <= floor < p2p_min_completion_rate, "
                f"got floor={self.p2p_completion_floor_pct!r} "
                f"min={self.p2p_min_completion_rate!r}"
            )

        if isinstance(self.p2p_new_account_age_days, bool) or not isinstance(
            self.p2p_new_account_age_days, int
        ):
            raise ValueError(
                "p2p_new_account_age_days must be an int, "
                f"got {type(self.p2p_new_account_age_days).__name__}"
            )
        if self.p2p_new_account_age_days <= 0:
            raise ValueError(
                f"p2p_new_account_age_days must be > 0, got {self.p2p_new_account_age_days!r}"
            )

        _require_finite("p2p_suspicious_threshold", self.p2p_suspicious_threshold)
        _require_finite("p2p_high_risk_threshold", self.p2p_high_risk_threshold)
        if not (
            0.0 < self.p2p_suspicious_threshold < self.p2p_high_risk_threshold < 1.0
        ):
            raise ValueError(
                "risk bands must satisfy 0 < suspicious < high_risk < 1, got "
                f"suspicious={self.p2p_suspicious_threshold!r} "
                f"high_risk={self.p2p_high_risk_threshold!r}"
            )

    @classmethod
    def retail_safe(cls) -> "AppConfig":
        """Recall-first preset for a retail seller protecting their own bank
        account.

        The cost of the two error types is wildly asymmetric here. A false
        positive means you skip one counterparty and pick another — 30 seconds
        lost. A false negative means dirty fiat lands in your account and the
        bank freezes it for weeks. So this preset deliberately trades precision
        for recall: tighter premium tolerance and lower category thresholds, so
        marginal traders get surfaced as SUSPICIOUS rather than waved through.

        Use the default ``AppConfig()`` for exchange-side monitoring, where
        false positives annoy real customers and precision matters more.
        """
        return cls(
            p2p_max_allowed_premium=0.008,   # warn earlier on any premium
            p2p_velocity_threshold=30.0,     # a brand-new account at 30/day is
                                             # already anomalous; aged accounts
                                             # stay protected by the 0.15 discount
            p2p_suspicious_threshold=0.25,   # surface marginal traders
            p2p_high_risk_threshold=0.55,    # escalate sooner
        )
