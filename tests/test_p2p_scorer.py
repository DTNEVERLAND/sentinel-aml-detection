"""Unit tests for P2PBehavioralScorer.

Covers the four required scenarios (clean retailer, laundering-premium
trader, high-velocity new runner, input validation) plus the engineering
guarantees: determinism, boundedness, frozen config, and the two spec fixes
(30-day divisor cap, aged-account velocity discount).
"""

import dataclasses
import math

import pytest

from config_loader import AppConfig
from core.p2p_scorer import (
    CATEGORY_CLEAN,
    CATEGORY_HIGH_RISK,
    CATEGORY_SUSPICIOUS,
    P2PBehavioralScorer,
    P2PTraderMetadata,
    evaluate_counterparty_risk,
)

CFG = AppConfig()


def _meta(**overrides) -> P2PTraderMetadata:
    """A baseline perfectly-ordinary retailer; override fields per test."""
    base = dict(
        trader_id="MY-TRADER-001",
        advertised_price_fiat=4.20,
        market_mid_price_fiat=4.20,
        account_age_days=400,
        total_orders_30d=45,
        completion_rate_pct=99.0,
    )
    base.update(overrides)
    return P2PTraderMetadata(**base)


# ── Scenario 1: clean retailer ──────────────────────────────────────────────


class TestCleanRetailer:
    def test_zero_score_and_clean_category(self):
        result = evaluate_counterparty_risk(_meta(), CFG)
        assert result["risk_score"] == 0.0
        assert result["category"] == CATEGORY_CLEAN
        assert result["reasons"] == []

    def test_small_premium_within_allowance_is_clean(self):
        # 1.0% premium — under the 1.5% allowance.
        result = evaluate_counterparty_risk(
            _meta(advertised_price_fiat=4.242, market_mid_price_fiat=4.20), CFG
        )
        assert result["components"]["premium"] == 0.0
        assert result["category"] == CATEGORY_CLEAN

    def test_discount_pricing_is_not_penalised(self):
        result = evaluate_counterparty_risk(_meta(advertised_price_fiat=4.00), CFG)
        assert result["components"]["premium"] == 0.0


# ── Scenario 2: aggressive laundering premium ───────────────────────────────


class TestLaunderingPremium:
    def test_deep_premium_is_high_risk_alone(self):
        # ~5.95% over mid: component saturates, premium alone crosses the band.
        result = evaluate_counterparty_risk(
            _meta(advertised_price_fiat=4.45, market_mid_price_fiat=4.20), CFG
        )
        assert result["components"]["premium"] == 1.0
        assert result["category"] == CATEGORY_HIGH_RISK
        assert any("dirty-fiat" in r for r in result["reasons"])

    def test_moderate_premium_is_suspicious(self):
        # 2.0% over mid: half-ramp above the calibrated 1.0% threshold.
        result = evaluate_counterparty_risk(
            _meta(advertised_price_fiat=4.284, market_mid_price_fiat=4.20), CFG
        )
        assert result["category"] == CATEGORY_SUSPICIOUS

    def test_premium_ramp_is_monotonic(self):
        scores = [
            evaluate_counterparty_risk(
                _meta(advertised_price_fiat=4.20 * (1 + p)), CFG
            )["risk_score"]
            for p in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08)
        ]
        assert scores == sorted(scores)


# ── Scenario 3: high-velocity new runner ────────────────────────────────────


class TestVelocityRunner:
    def test_new_account_industrial_velocity_is_high_risk(self):
        # 6-day-old account, 720 orders → 120/day against a 50/day threshold.
        result = evaluate_counterparty_risk(
            _meta(account_age_days=6, total_orders_30d=720), CFG
        )
        assert result["components"]["velocity"] == 1.0
        assert result["category"] == CATEGORY_HIGH_RISK

    def test_window_divisor_is_capped_at_30_days(self):
        # Spec-bug regression: 300-day-old account with 600 orders in the
        # 30-day window is 20/day, NOT 2/day. Dividing by full account age
        # would hide every old account from this signal.
        result = evaluate_counterparty_risk(
            _meta(account_age_days=300, total_orders_30d=600), CFG
        )
        assert result["signals"]["daily_velocity"] == 20.0
        assert result["signals"]["effective_window_days"] == 30

    def test_aged_high_volume_alone_is_clean(self):
        # Calibration property (red-team driven): real market makers run
        # 57-298 orders/day. An aged account at 100/day with NO premium is a
        # legitimate merchant, not a mule — it must NOT be flagged on volume
        # alone. Velocity scores only the small aged discount.
        result = evaluate_counterparty_risk(
            _meta(account_age_days=200, total_orders_30d=3000), CFG
        )
        assert result["components"]["velocity"] == pytest.approx(0.15)
        assert result["category"] == CATEGORY_CLEAN

    def test_bought_aged_account_with_premium_is_flagged(self):
        # The bought-aged-account mule is caught by its PREMIUM, not its
        # volume: same aged high-volume account but paying 2.5% over mid.
        result = evaluate_counterparty_risk(
            _meta(
                account_age_days=200,
                total_orders_30d=3000,
                advertised_price_fiat=4.305,  # +2.5%
            ),
            CFG,
        )
        assert result["category"] in (CATEGORY_SUSPICIOUS, CATEGORY_HIGH_RISK)

    def test_velocity_soft_shoulder_blocks_just_under_gaming(self):
        # Evasion counter: a new account parked at 49/day (just under the
        # 50/day threshold) still accrues velocity risk via the soft shoulder.
        result = evaluate_counterparty_risk(
            _meta(account_age_days=20, total_orders_30d=980), CFG  # 49/day
        )
        assert result["components"]["velocity"] > 0.0

    def test_new_account_with_low_velocity_is_clean(self):
        result = evaluate_counterparty_risk(
            _meta(account_age_days=10, total_orders_30d=80), CFG  # 8/day
        )
        assert result["components"]["velocity"] == 0.0
        assert result["category"] == CATEGORY_CLEAN


# ── Completion-rate behaviour ───────────────────────────────────────────────


class TestCompletionDecay:
    def test_low_completion_alone_never_reaches_suspicious(self):
        # Design property: a flaky counterparty is not a launderer.
        result = evaluate_counterparty_risk(_meta(completion_rate_pct=80.0), CFG)
        assert result["components"]["completion"] == 1.0
        assert result["risk_score"] < CFG.p2p_suspicious_threshold
        assert result["category"] == CATEGORY_CLEAN

    def test_completion_compounds_with_other_signals(self):
        with_completion = evaluate_counterparty_risk(
            _meta(advertised_price_fiat=4.45, completion_rate_pct=85.0), CFG
        )
        without_completion = evaluate_counterparty_risk(
            _meta(advertised_price_fiat=4.45), CFG
        )
        assert with_completion["risk_score"] > without_completion["risk_score"]


# ── Combined worst case ─────────────────────────────────────────────────────


def test_full_mule_profile_scores_high_and_bounded():
    result = evaluate_counterparty_risk(
        _meta(
            advertised_price_fiat=4.50,
            account_age_days=5,
            total_orders_30d=900,
            completion_rate_pct=82.0,
        ),
        CFG,
    )
    assert result["category"] == CATEGORY_HIGH_RISK
    assert 0.9 < result["risk_score"] < 1.0


# ── Scenario 4: input validation ────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"trader_id": ""},
            {"trader_id": "   "},
            {"advertised_price_fiat": 0.0},
            {"advertised_price_fiat": -4.2},
            {"market_mid_price_fiat": 0.0},
            {"market_mid_price_fiat": -1.0},
            {"account_age_days": 0},
            {"account_age_days": -5},
            {"total_orders_30d": -1},
            {"completion_rate_pct": -0.1},
            {"completion_rate_pct": 100.1},
            {"advertised_price_fiat": float("nan")},
            {"market_mid_price_fiat": float("inf")},
            {"completion_rate_pct": float("nan")},
        ],
    )
    def test_corrupt_metadata_raises_value_error(self, overrides):
        with pytest.raises(ValueError):
            evaluate_counterparty_risk(_meta(**overrides), CFG)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"p2p_max_allowed_premium": -0.01},
            {"p2p_max_allowed_premium": 0.0},
            {"p2p_max_allowed_premium": 1.5},
            {"p2p_velocity_threshold": 0.0},
            {"p2p_velocity_threshold": -10.0},
            {"p2p_min_completion_rate": 0.0},
            {"p2p_min_completion_rate": 101.0},
            {"p2p_completion_floor_pct": 96.0},  # >= min_completion_rate
            {"p2p_new_account_age_days": 0},
            {"p2p_suspicious_threshold": 0.8},  # inverted bands
            {"p2p_high_risk_threshold": 1.0},
        ],
    )
    def test_invalid_config_raises_value_error(self, kwargs):
        with pytest.raises(ValueError):
            AppConfig(**kwargs)


# ── Engineering guarantees ──────────────────────────────────────────────────


class TestGuarantees:
    def test_deterministic_repeated_calls_identical(self):
        meta = _meta(
            advertised_price_fiat=4.38, account_age_days=12, total_orders_30d=400
        )
        scorer = P2PBehavioralScorer()
        first = scorer.evaluate_counterparty_risk(meta, CFG)
        for _ in range(50):
            assert scorer.evaluate_counterparty_risk(meta, CFG) == first

    def test_score_bounded_across_input_grid(self):
        for premium in (0.9, 1.0, 1.02, 1.1, 2.0):
            for age in (1, 15, 30, 365):
                for orders in (0, 100, 5000, 10**6):
                    for rate in (0.0, 50.0, 95.0, 100.0):
                        result = evaluate_counterparty_risk(
                            _meta(
                                advertised_price_fiat=4.20 * premium,
                                account_age_days=age,
                                total_orders_30d=orders,
                                completion_rate_pct=rate,
                            ),
                            CFG,
                        )
                        assert 0.0 <= result["risk_score"] <= 1.0
                        assert result["category"] in (
                            CATEGORY_CLEAN,
                            CATEGORY_SUSPICIOUS,
                            CATEGORY_HIGH_RISK,
                        )

    def test_config_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            CFG.p2p_max_allowed_premium = 0.5  # type: ignore[misc]

    def test_metadata_is_frozen(self):
        meta = _meta()
        with pytest.raises(dataclasses.FrozenInstanceError):
            meta.advertised_price_fiat = 9.99  # type: ignore[misc]

    def test_result_components_are_finite(self):
        result = evaluate_counterparty_risk(_meta(advertised_price_fiat=999999.0), CFG)
        assert math.isfinite(result["risk_score"])
        assert all(math.isfinite(v) for v in result["components"].values())


# ── Retail-safe preset (recall-first) ───────────────────────────────────────


class TestRetailSafePreset:
    RS = AppConfig.retail_safe()

    def test_preset_is_valid_and_more_conservative(self):
        assert self.RS.p2p_suspicious_threshold < CFG.p2p_suspicious_threshold
        assert self.RS.p2p_high_risk_threshold < CFG.p2p_high_risk_threshold
        assert self.RS.p2p_max_allowed_premium < CFG.p2p_max_allowed_premium

    def test_catches_borderline_new_mule_that_balanced_misses(self):
        # New account, ~28/day, tiny 0.6% premium — exchange profile waves it
        # through; retail-safe must surface it as at least SUSPICIOUS.
        meta = _meta(
            advertised_price_fiat=4.20 * 1.006,
            account_age_days=22,
            total_orders_30d=620,
            completion_rate_pct=99.8,
        )
        assert evaluate_counterparty_risk(meta, CFG)["category"] == CATEGORY_CLEAN
        assert evaluate_counterparty_risk(meta, self.RS)["category"] != CATEGORY_CLEAN

    def test_does_not_flag_aged_at_market_merchant(self):
        # Established merchant: aged, at-mid, high volume, high finish.
        # Recall-first must NOT turn this into noise.
        meta = _meta(
            advertised_price_fiat=4.20,
            account_age_days=400,
            total_orders_30d=1700,
            completion_rate_pct=99.8,
        )
        assert evaluate_counterparty_risk(meta, self.RS)["category"] == CATEGORY_CLEAN
