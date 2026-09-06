-- ============================================================
-- PHASE 1 -- WATERFALL & CREDIT ENHANCEMENT LAYER
-- Adds structure-level fields to raw.deals and a new long-format
-- raw.monthly_waterfall table, plus calc.* views for CE monitoring
-- and MRR compliance.
--
-- Run this AFTER 01-04_schema*.sql have already been applied.
-- Safe to re-run: ALTER ... ADD COLUMN IF NOT EXISTS and
-- CREATE OR REPLACE VIEW are used throughout.
--
-- ------------------------------------------------------------
-- ASSUMPTIONS -- read before trusting any number out of this layer
-- ------------------------------------------------------------
-- 1. deal_type: NOT added as a new column. raw.deals.instrument_type
--    (PTC/DA) already captures this distinction from 01_schema_raw.sql --
--    adding a second column with the same meaning would just create a
--    sync-drift risk. All Phase 1+ logic reads instrument_type.
--
-- 2. tranche: modeled as ONE tranche per deal row (Senior, Subordinate,
--    or NULL for DA). TO BE CONFIRMED / KNOWN SIMPLIFICATION: a real PTC
--    issuance often carries senior AND subordinate tranches backed by the
--    SAME underlying pool, linked to each other. This schema has no
--    pool_id to link a paired senior/subordinate tranche back to a common
--    pool -- each tranche is generated here as an independent deal row.
--    That's fine for demonstrating waterfall mechanics and CE monitoring,
--    but it means a Subordinate-tranche row cannot look up "its" senior
--    tranche's outstanding POS. See the ce_cover_ratio note below for the
--    concrete consequence. A production version would add
--    raw.deal_pools(pool_id) and have raw.deals.pool_id + tranche instead
--    of tranche living alone on the deal.
--
-- 3. credit_enhancement_type / credit_enhancement_initial_amount: each
--    deal is given ONE dominant CE mechanism (Cash Collateral /
--    Overcollateralization / First Loss Guarantee / None), consistent
--    with how the synthetic generator now sets first_loss_facility_pct
--    and overcollateral_pct (only the pct matching the dominant type is
--    non-zero; Cash Collateral uses a freshly generated pct since no
--    existing column represented it). DA deals default to 'None' --
--    TO BE CONFIRMED: real DA transactions can occasionally carry an
--    originator guarantee, but formal tranched CE is a PTC-market feature;
--    modeling DA CE as None is a simplification, not a market fact.
--
-- 4. mrr_percent: RBI's actual Minimum Retention Requirement varies by
--    loan tenor/exposure category (broadly 5% vs 10% tiers). This schema
--    does not track loan tenor, so mrr_percent is generated as a flat
--    random pick between the two common thresholds rather than derived
--    from tenor. TO BE CONFIRMED against the real MRR matrix before using
--    this for anything beyond a demo.
--
-- 5. monthly_waterfall priority order (servicing fee -> senior interest ->
--    senior principal -> subordinate interest -> subordinate principal ->
--    excess spread to originator) is STANDARD MARKET CONVENTION for a
--    sequential-pay Indian PTC structure, but has NOT been confirmed
--    against any specific deal's actual Payment Waterfall clause in its
--    PTC/DA agreement. Trustee/rating-agency fees are treated as one-time
--    deal-level execution costs (already in raw.deals) rather than a
--    recurring monthly waterfall line -- TO BE CONFIRMED whether the real
--    deals also deduct a recurring trustee fee inside the monthly
--    waterfall (common in practice) rather than only up front.
--
-- 6. CE draws in this table are a PROXY, not a fully modeled shortfall.
--    A month is flagged "rough" using that month's npa_amount (already in
--    monthly_mis) as a stand-in for pool deterioration, since DPD buckets
--    don't exist until Phase 2. senior_interest_paid/senior_principal_paid
--    are still taken as fully scheduled (matching monthly_mis, which is
--    not itself reduced in a stress month yet) -- the CE draw plugs a
--    modeled shortfall in collections FROM THE POOL, not a shortfall in
--    what investors were paid. Once Phase 2 ties CE draws to actual rising
--    90+ DPD and reduces realized collections accordingly, this Phase 1
--    approximation should be revisited so the two are consistent.
--
-- 7. CE does not replenish in this model (no excess-spread-builds-OC
--    mechanic) -- ce_closing_balance only ever draws down from
--    credit_enhancement_initial_amount. TO BE CONFIRMED: real
--    overcollateralization structures can rebuild CE from trapped excess
--    spread; not modeled here for simplicity.
--
-- 8. ce_cover_ratio = ce_closing_balance / outstanding senior POS. For a
--    Senior-tranche row this is the deal's own closing_pos_investor_share.
--    For a Subordinate-tranche row there is no linked senior POS to divide
--    by (see assumption #2) -- ce_cover_ratio is left NULL for those rows
--    rather than faking a proxy denominator. DA deals have no waterfall
--    row at all (see #9).
--
-- 9. DA (Direct Assignment) deals are EXCLUDED from raw.monthly_waterfall
--    entirely. Standard market/RBI treatment of DA is a proportional
--    co-ownership transfer of receivables to the assignee, not a tranched
--    PTC issuance with a payment waterfall or CE structure -- so "no
--    waterfall row for DA" is a modeling choice grounded in market
--    convention, not an oversight.
-- ============================================================

-- calc schema may not exist yet if this file is run before
-- 02_schema_calc.sql (load_to_postgres.py now runs this file right after
-- 01_schema_raw.sql, before any CSV COPY, so raw.deals has its new
-- columns in place before deals.csv is loaded). Idempotent either way.
CREATE SCHEMA IF NOT EXISTS calc;

-- ------------------------------------------------------------
-- 1. Structure-level fields on raw.deals
-- ------------------------------------------------------------
ALTER TABLE raw.deals
    ADD COLUMN IF NOT EXISTS tranche TEXT
        CHECK (tranche IN ('Senior', 'Subordinate') OR tranche IS NULL),
    ADD COLUMN IF NOT EXISTS credit_enhancement_type TEXT
        CHECK (credit_enhancement_type IN
            ('Cash Collateral', 'Overcollateralization', 'First Loss Guarantee', 'None')),
    ADD COLUMN IF NOT EXISTS credit_enhancement_initial_amount NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS mrr_percent NUMERIC(6,4);

-- Enforce the PTC/DA <-> tranche relationship described in assumption #2.
-- Dropped and re-added so this file can be re-run safely.
ALTER TABLE raw.deals DROP CONSTRAINT IF EXISTS chk_tranche_matches_instrument;
ALTER TABLE raw.deals
    ADD CONSTRAINT chk_tranche_matches_instrument CHECK (
        (instrument_type = 'DA' AND tranche IS NULL) OR
        (instrument_type = 'PTC' AND tranche IN ('Senior', 'Subordinate'))
    );

-- ------------------------------------------------------------
-- 2. monthly_waterfall
-- Long format: one row per (PTC deal, month). See assumptions above
-- for why DA deals never appear here and why priority order/CE draw
-- logic is flagged TO BE CONFIRMED.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.monthly_waterfall (
    id                              BIGSERIAL PRIMARY KEY,
    deal_id                         TEXT NOT NULL REFERENCES raw.deals (deal_id),
    month                           DATE NOT NULL,

    -- Priority-order waterfall lines (see assumption #5)
    collections_available           NUMERIC(14,2),
    servicing_fee_paid              NUMERIC(14,2),
    senior_interest_paid            NUMERIC(14,2),
    senior_principal_paid           NUMERIC(14,2),
    subordinate_interest_paid       NUMERIC(14,2),
    subordinate_principal_paid      NUMERIC(14,2),
    excess_spread_to_originator     NUMERIC(14,2),

    -- Credit enhancement roll-forward (see assumptions #6, #7, #8)
    ce_opening_balance              NUMERIC(14,2),
    ce_drawn_this_month             NUMERIC(14,2) DEFAULT 0,
    ce_closing_balance              NUMERIC(14,2),
    ce_cover_ratio                  NUMERIC(10,4),   -- NULL for Subordinate-tranche rows

    UNIQUE (deal_id, month)
);

CREATE INDEX IF NOT EXISTS idx_monthly_waterfall_deal_month
    ON raw.monthly_waterfall (deal_id, month);
CREATE INDEX IF NOT EXISTS idx_monthly_waterfall_month
    ON raw.monthly_waterfall (month);

-- ------------------------------------------------------------
-- 3. calc.* views
-- ------------------------------------------------------------

-- CE cover ratio trend per deal (chart-ready: one row per deal per month).
CREATE OR REPLACE VIEW calc.ce_cover_ratio_trend AS
SELECT
    w.deal_id,
    d.deal_name,
    d.investor_name,
    d.tranche,
    d.credit_enhancement_type,
    w.month,
    w.ce_opening_balance,
    w.ce_drawn_this_month,
    w.ce_closing_balance,
    w.ce_cover_ratio
FROM raw.monthly_waterfall w
JOIN raw.deals d ON d.deal_id = w.deal_id
ORDER BY w.deal_id, w.month;

-- Deals where CE has been drawn in the last 3/6/12 months, relative to
-- the latest month present in monthly_waterfall (not wall-clock "today",
-- since this is a synthetic/point-in-time dataset).
CREATE OR REPLACE VIEW calc.ce_drawn_recent AS
WITH latest_month AS (
    SELECT MAX(month) AS m FROM raw.monthly_waterfall
)
SELECT
    d.deal_id,
    d.deal_name,
    d.investor_name,
    d.credit_enhancement_type,
    SUM(w.ce_drawn_this_month) FILTER (
        WHERE w.month > (SELECT m FROM latest_month) - INTERVAL '3 months'
    ) AS ce_drawn_last_3m,
    SUM(w.ce_drawn_this_month) FILTER (
        WHERE w.month > (SELECT m FROM latest_month) - INTERVAL '6 months'
    ) AS ce_drawn_last_6m,
    SUM(w.ce_drawn_this_month) FILTER (
        WHERE w.month > (SELECT m FROM latest_month) - INTERVAL '12 months'
    ) AS ce_drawn_last_12m
FROM raw.monthly_waterfall w
JOIN raw.deals d ON d.deal_id = w.deal_id
GROUP BY d.deal_id, d.deal_name, d.investor_name, d.credit_enhancement_type
HAVING COALESCE(SUM(w.ce_drawn_this_month) FILTER (
    WHERE w.month > (SELECT m FROM latest_month) - INTERVAL '12 months'
), 0) > 0;

-- MRR compliance status per deal.
-- TO BE CONFIRMED: threshold hardcoded at 5% (RBI's lower MRR tier).
-- Real compliance depends on which tier (5%/10%) applies to that deal's
-- loan tenor, which isn't tracked here -- see assumption #4.
CREATE OR REPLACE VIEW calc.mrr_compliance AS
SELECT
    d.deal_id,
    d.deal_name,
    d.investor_name,
    d.instrument_type,
    d.mrr_percent,
    CASE
        WHEN d.mrr_percent IS NULL THEN 'Unknown'
        WHEN d.mrr_percent >= 0.05 THEN 'Compliant'
        ELSE 'Non-Compliant'
    END AS mrr_compliance_status
FROM raw.deals d;
