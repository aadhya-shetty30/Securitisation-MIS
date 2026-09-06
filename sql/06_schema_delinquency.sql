-- ============================================================
-- PHASE 2 -- DELINQUENCY, DEFAULT & PREPAYMENT ANALYTICS
-- Adds DPD bucket tracking (raw.monthly_delinquency), a scheduled-vs-
-- actual principal split on raw.monthly_mis (needed for CPR), and
-- calc.* views for roll rates, vintage default curves, and SMM/CPR.
--
-- Run this AFTER 01-05_schema*.sql have already been applied.
-- Safe to re-run: ALTER ... ADD COLUMN IF NOT EXISTS, CREATE TABLE IF
-- NOT EXISTS, and CREATE OR REPLACE VIEW are used throughout.
--
-- ------------------------------------------------------------
-- ASSUMPTIONS -- read before trusting any number out of this layer
-- ------------------------------------------------------------
-- 1. scheduled_principal_payout (new column on raw.monthly_mis): splits
--    the previously-single total_principal_payout into a contractual
--    "scheduled" piece and an implied prepayment piece
--    (total_principal_payout - scheduled_principal_payout). This is a
--    BEHAVIOR CHANGE from the Phase 1 generator, which computed
--    total_principal_payout as a flat amount off the ORIGINAL deal
--    amount every month -- inconsistent with the geometrically-decaying
--    POS it was supposedly funding, and with no prepayment concept at
--    all. Phase 2 fixes scheduled_principal_payout to be based on the
--    deal's actual declining balance that month, and adds an explicit
--    modeled prepayment on top. Regenerating data is required; this
--    is not backward compatible with pre-Phase-2 monthly_mis.csv rows.
--
-- 2. raw.monthly_delinquency buckets are sized against
--    total_pool_outstanding (the "100%" pool figure), consistent with
--    how npa_amount is already labeled "NPA (100%)" in 01_schema_raw.sql
--    -- i.e., DPD is a pool asset-quality measure, not scaled to the
--    investor's proportional share.
--
-- 3. DPD bucket evolution is a SIMPLE aggregate Markov-style roll
--    forward per deal (fixed transition/cure/write-off probabilities by
--    a "credit trajectory" archetype: Clean / Deteriorating /
--    Recovering), not a loan-level simulation. TO BE CONFIRMED /
--    KNOWN SIMPLIFICATION: cures are modeled as jumping straight back
--    to the 0-DPD bucket rather than stepping down one bucket at a
--    time, and a written-off slice of the 90+ bucket is added back into
--    the 0-DPD bucket so buckets keep summing to total_pool_outstanding
--    (which the generator computes independently and does not itself
--    shrink when principal is written off). A production model would
--    tie total_pool_outstanding down when defaults occur instead of
--    treating cumulative_default_amount as a side memo.
--
-- 4. Roll rates (calc.roll_rate_*) are computed from snapshot bucket
--    BALANCES only (this_month's higher bucket / last_month's adjacent
--    lower bucket), which is the STANDARD MIS approximation used when
--    loan-level roll tracing isn't available. It can over/understate the
--    true flow when new-formation and cures happen in the same bucket
--    in the same month -- TO BE CONFIRMED against a loan-level roll-rate
--    calc if one becomes available.
--
-- 5. CPR/SMM: SMM = max(actual principal - scheduled principal, 0) /
--    (beginning POS - scheduled principal); CPR = 1 - (1 - SMM)^12.
--    This is the standard MBS/ABS convention. TO BE CONFIRMED: what
--    counts as "scheduled" here is this synthetic generator's own flat
--    contractual amortization rate applied to the declining balance --
--    a real pool's scheduled amortization would follow the underlying
--    loans' actual EMI/contractual schedules (which can vary month to
--    month), not a single flat rate. Prepayment vs scheduled amortization
--    in the real dataset should be re-derived from the actual source
--    once available.
--
-- 6. WAL is NOT computed in SQL -- see python/risk_metrics.py. It uses
--    a closed-form approximation (WAL years = 1 / (12 * constant
--    monthly paydown rate)) assuming the pool runs off forever at its
--    current combined scheduled+prepayment rate. This is a standard
--    simplification for a constant-CPR assumption, not a full projected
--    cash-flow schedule -- adequate for a demo trend, explicitly not a
--    precision figure. Recomputed per month as the trailing rate updates,
--    per the "recomputed monthly" requirement.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Scheduled vs actual principal on raw.monthly_mis
-- ------------------------------------------------------------
ALTER TABLE raw.monthly_mis
    ADD COLUMN IF NOT EXISTS scheduled_principal_payout NUMERIC(14,2);

-- ------------------------------------------------------------
-- 2. monthly_delinquency
-- Long format: one row per deal per month, for ALL instrument types
-- (DPD is a pool asset-quality measure regardless of PTC/DA funding).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.monthly_delinquency (
    id                          BIGSERIAL PRIMARY KEY,
    deal_id                     TEXT NOT NULL REFERENCES raw.deals (deal_id),
    month                       DATE NOT NULL,

    pos_0dpd                    NUMERIC(14,2),
    pos_1_30dpd                 NUMERIC(14,2),
    pos_31_60dpd                NUMERIC(14,2),
    pos_61_90dpd                NUMERIC(14,2),
    pos_90plus_dpd              NUMERIC(14,2),
    cumulative_default_amount   NUMERIC(14,2),

    UNIQUE (deal_id, month)
);

CREATE INDEX IF NOT EXISTS idx_monthly_delinquency_deal_month
    ON raw.monthly_delinquency (deal_id, month);
CREATE INDEX IF NOT EXISTS idx_monthly_delinquency_month
    ON raw.monthly_delinquency (month);

-- ------------------------------------------------------------
-- 3. calc.* views
-- ------------------------------------------------------------

-- Per-deal roll rates (see assumption #4 for the balance-snapshot method).
CREATE OR REPLACE VIEW calc.roll_rate_deal_level AS
WITH ordered AS (
    SELECT
        deal_id, month, pos_0dpd, pos_1_30dpd, pos_31_60dpd, pos_61_90dpd, pos_90plus_dpd,
        LAG(pos_0dpd)    OVER w AS prev_0dpd,
        LAG(pos_1_30dpd) OVER w AS prev_1_30dpd,
        LAG(pos_31_60dpd) OVER w AS prev_31_60dpd,
        LAG(pos_61_90dpd) OVER w AS prev_61_90dpd
    FROM raw.monthly_delinquency
    WINDOW w AS (PARTITION BY deal_id ORDER BY month)
)
SELECT
    deal_id, month,
    CASE WHEN prev_0dpd > 0    THEN pos_1_30dpd  / prev_0dpd    END AS roll_0_to_30,
    CASE WHEN prev_1_30dpd > 0 THEN pos_31_60dpd / prev_1_30dpd END AS roll_30_to_60,
    CASE WHEN prev_31_60dpd > 0 THEN pos_61_90dpd / prev_31_60dpd END AS roll_60_to_90,
    CASE WHEN prev_61_90dpd > 0 THEN pos_90plus_dpd / prev_61_90dpd END AS roll_90_to_90plus
FROM ordered;

-- Pool-wide roll rates: sum balances across ALL deals first, then take
-- the ratio (weighted by outstanding balance, not a simple average of
-- per-deal ratios).
CREATE OR REPLACE VIEW calc.roll_rate_pool_wide AS
WITH monthly_totals AS (
    SELECT month,
        SUM(pos_0dpd) AS total_0dpd, SUM(pos_1_30dpd) AS total_1_30dpd,
        SUM(pos_31_60dpd) AS total_31_60dpd, SUM(pos_61_90dpd) AS total_61_90dpd,
        SUM(pos_90plus_dpd) AS total_90plus
    FROM raw.monthly_delinquency
    GROUP BY month
),
with_lag AS (
    SELECT month, total_0dpd, total_1_30dpd, total_31_60dpd, total_61_90dpd, total_90plus,
        LAG(total_0dpd)    OVER (ORDER BY month) AS prev_0dpd,
        LAG(total_1_30dpd) OVER (ORDER BY month) AS prev_1_30dpd,
        LAG(total_31_60dpd) OVER (ORDER BY month) AS prev_31_60dpd,
        LAG(total_61_90dpd) OVER (ORDER BY month) AS prev_61_90dpd
    FROM monthly_totals
)
SELECT
    month,
    CASE WHEN prev_0dpd > 0    THEN total_1_30dpd  / prev_0dpd    END AS roll_0_to_30,
    CASE WHEN prev_1_30dpd > 0 THEN total_31_60dpd / prev_1_30dpd END AS roll_30_to_60,
    CASE WHEN prev_31_60dpd > 0 THEN total_61_90dpd / prev_31_60dpd END AS roll_60_to_90,
    CASE WHEN prev_61_90dpd > 0 THEN total_90plus / prev_61_90dpd END AS roll_90_to_90plus
FROM with_lag;

-- Same, split by product (underlying) and instrument type.
CREATE OR REPLACE VIEW calc.roll_rate_by_product AS
WITH monthly_totals AS (
    SELECT d.underlying AS product, d.instrument_type, md.month,
        SUM(md.pos_0dpd) AS total_0dpd, SUM(md.pos_1_30dpd) AS total_1_30dpd,
        SUM(md.pos_31_60dpd) AS total_31_60dpd, SUM(md.pos_61_90dpd) AS total_61_90dpd,
        SUM(md.pos_90plus_dpd) AS total_90plus
    FROM raw.monthly_delinquency md
    JOIN raw.deals d ON d.deal_id = md.deal_id
    GROUP BY d.underlying, d.instrument_type, md.month
),
with_lag AS (
    SELECT product, instrument_type, month,
        total_0dpd, total_1_30dpd, total_31_60dpd, total_61_90dpd, total_90plus,
        LAG(total_0dpd)    OVER (PARTITION BY product, instrument_type ORDER BY month) AS prev_0dpd,
        LAG(total_1_30dpd) OVER (PARTITION BY product, instrument_type ORDER BY month) AS prev_1_30dpd,
        LAG(total_31_60dpd) OVER (PARTITION BY product, instrument_type ORDER BY month) AS prev_31_60dpd,
        LAG(total_61_90dpd) OVER (PARTITION BY product, instrument_type ORDER BY month) AS prev_61_90dpd
    FROM monthly_totals
)
SELECT
    product, instrument_type, month,
    CASE WHEN prev_0dpd > 0    THEN total_1_30dpd  / prev_0dpd    END AS roll_0_to_30,
    CASE WHEN prev_1_30dpd > 0 THEN total_31_60dpd / prev_1_30dpd END AS roll_30_to_60,
    CASE WHEN prev_31_60dpd > 0 THEN total_61_90dpd / prev_31_60dpd END AS roll_60_to_90,
    CASE WHEN prev_61_90dpd > 0 THEN total_90plus / prev_61_90dpd END AS roll_90_to_90plus
FROM with_lag;

-- Per-deal vintage performance: month-on-book + cumulative default rate,
-- keyed to the deal's origination quarter (raw.deals.period) as its
-- vintage label.
CREATE OR REPLACE VIEW calc.deal_vintage_performance AS
SELECT
    d.deal_id, d.deal_name, d.period AS vintage_quarter, d.underlying, d.instrument_type,
    md.month,
    (EXTRACT(YEAR  FROM md.month)::INT - EXTRACT(YEAR  FROM date_trunc('month', d.borrowing_date))::INT) * 12
        + (EXTRACT(MONTH FROM md.month)::INT - EXTRACT(MONTH FROM date_trunc('month', d.borrowing_date))::INT)
        AS month_on_book,
    md.cumulative_default_amount,
    d.amount_securitised_deal_date,
    CASE WHEN d.amount_securitised_deal_date > 0
         THEN md.cumulative_default_amount / d.amount_securitised_deal_date
    END AS cumulative_default_rate
FROM raw.monthly_delinquency md
JOIN raw.deals d ON d.deal_id = md.deal_id;

-- Vintage curve ready for charting: cumulative default rate by vintage
-- quarter vs month-on-book, aggregated (weighted) across all deals in
-- that vintage.
CREATE OR REPLACE VIEW calc.vintage_default_curve AS
SELECT
    vintage_quarter, month_on_book,
    SUM(cumulative_default_amount) AS total_cumulative_default,
    SUM(amount_securitised_deal_date) AS total_vintage_amount,
    CASE WHEN SUM(amount_securitised_deal_date) > 0
         THEN SUM(cumulative_default_amount) / SUM(amount_securitised_deal_date)
    END AS cumulative_default_rate
FROM calc.deal_vintage_performance
GROUP BY vintage_quarter, month_on_book;

-- Per-deal SMM / annualized CPR (see assumption #5). WAL is deliberately
-- NOT computed here -- see python/risk_metrics.py and assumption #6.
CREATE OR REPLACE VIEW calc.deal_smm_cpr AS
WITH ordered AS (
    SELECT
        deal_id, month, closing_pos_investor_share, total_principal_payout, scheduled_principal_payout,
        LAG(closing_pos_investor_share) OVER (PARTITION BY deal_id ORDER BY month) AS beginning_pos
    FROM raw.monthly_mis
    WHERE data_status = 'Available'
),
smm_calc AS (
    SELECT
        deal_id, month, beginning_pos, scheduled_principal_payout,
        GREATEST(total_principal_payout - scheduled_principal_payout, 0) AS prepayment_amount,
        CASE WHEN (beginning_pos - scheduled_principal_payout) > 0
             THEN GREATEST(total_principal_payout - scheduled_principal_payout, 0)
                  / (beginning_pos - scheduled_principal_payout)
        END AS smm
    FROM ordered
    WHERE beginning_pos IS NOT NULL
)
SELECT
    deal_id, month, beginning_pos, scheduled_principal_payout, prepayment_amount, smm,
    CASE WHEN smm IS NOT NULL THEN 1 - POWER(1 - smm, 12) END AS cpr_annualized
FROM smm_calc;
