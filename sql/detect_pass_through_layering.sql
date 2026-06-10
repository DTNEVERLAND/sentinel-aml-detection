-- ─────────────────────────────────────────────────────────────────────────────
-- Sentinel · Pass-Through Layering Detection
--
-- Pairs fiat withdrawals with ALL same-account volatile-alt deposits made in
-- the prior 2 hours. Flag fires when the exit drains 95–105% of that value.
--
-- Owner:   Risk & Compliance Engineering
-- Cadence: every 5 minutes (Airflow DAG: aml_tripwires)
-- Target:  PostgreSQL 15 (exchange ledger schema)
--
-- Design rationale for every threshold: docs/design-decisions.md
-- ─────────────────────────────────────────────────────────────────────────────

WITH alt_deposits AS (
  -- Step 1. Confirmed inbound transfers of volatile alts.
  -- 26h window = 24h withdrawal scan + 2h join lookback, so a withdrawal at
  -- the edge of its own window can still see its funding deposits.
  SELECT
    user_id,
    txn_id      AS deposit_txn,
    asset       AS deposit_asset,
    amount_usd  AS deposit_value,
    created_at  AS deposit_ts
  FROM ledger.deposits
  WHERE asset IN ('ADA', 'XRP')
    AND status = 'confirmed'
    AND created_at >= NOW() - INTERVAL '26 hours'
),

fiat_withdrawals AS (
  -- Step 2. Pending or processing fiat off-ramps — fiat that has NOT settled
  -- yet. This is the only window where a hold still does anything.
  SELECT
    user_id,
    txn_id      AS withdrawal_txn,
    asset       AS fiat_asset,
    amount_usd  AS withdrawal_value,
    created_at  AS withdrawal_ts
  FROM ledger.withdrawals
  WHERE asset IN ('MYR', 'IDR', 'ZAR', 'EUR', 'GBP')
    AND status IN ('pending', 'processing')
    AND created_at >= NOW() - INTERVAL '24 hours'
),

paired_flows AS (
  -- Step 3. Pair each fiat exit with every same-user deposit in the prior 2h,
  -- then aggregate the deposit side. Two things fall out of the aggregation:
  --   · split-deposit structuring (3 small deposits, 1 exit) collapses into
  --     one row instead of evading or triple-firing the rule;
  --   · the 2-hour window lives in the join, not a later filter.
  SELECT
    f.user_id,
    f.withdrawal_txn,
    f.fiat_asset,
    f.withdrawal_value,
    ARRAY_AGG(d.deposit_txn ORDER BY d.deposit_ts)   AS deposit_txns,
    ARRAY_AGG(DISTINCT d.deposit_asset)              AS deposit_assets,
    SUM(d.deposit_value)                             AS deposit_value,
    EXTRACT(EPOCH FROM (f.withdrawal_ts - MIN(d.deposit_ts))) / 60.0
                                                     AS minutes_elapsed,
    ROUND(f.withdrawal_value / NULLIF(SUM(d.deposit_value), 0), 4)
                                                     AS withdrawal_ratio
  FROM fiat_withdrawals f
  JOIN alt_deposits d
    ON  d.user_id    = f.user_id
    AND d.deposit_ts <  f.withdrawal_ts
    AND d.deposit_ts >= f.withdrawal_ts - INTERVAL '2 hours'
  GROUP BY f.user_id, f.withdrawal_txn, f.fiat_asset,
           f.withdrawal_value, f.withdrawal_ts
)

-- Step 4. Tripwire: the exit drains 95–105% of everything deposited in the
-- prior 2 hours. The band matters in both directions:
--   · below 95%, the user kept a real position — probably a trader;
--   · above 105%, the withdrawal is bigger than the deposits, i.e. funded by
--     pre-existing balance that merely coincides with a small deposit.
--     Without the upper bound, that false-positive class fires constantly.
SELECT
  user_id,
  deposit_assets,
  fiat_asset,
  deposit_txns,
  withdrawal_txn,
  deposit_value,
  withdrawal_value,
  minutes_elapsed,
  withdrawal_ratio,
  'PASS_THROUGH_LAYERING' AS risk_flag
FROM paired_flows
WHERE withdrawal_ratio BETWEEN 0.95 AND 1.05
ORDER BY minutes_elapsed ASC;

-- Production note: alert dedup is handled downstream, not here. The pipeline
-- anti-joins this result against aml.flagged_events on withdrawal_txn before
-- calling the Risk API, and the API itself is idempotent on evidence_ref —
-- so the 5-minute cadence never double-holds or double-pages an analyst.
