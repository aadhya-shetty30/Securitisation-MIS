-- ============================================================
-- PHASE 4 -- FUNDING COST / ALM LENS
-- Adds a stated tenor to raw.deals (needed for both the all-in-cost
-- amortization below and the WAL-vs-tenor ALM mismatch check on the
-- dashboard), plus a calc.deal_funding_cost view.
--
-- Run this AFTER 01-06_schema*.sql have already been applied.
-- Safe to re-run: ALTER ... ADD COLUMN IF NOT EXISTS and
-- CREATE OR REPLACE VIEW are used throughout.
--
-- ------------------------------------------------------------
-- ASSUMPTIONS -- read before trusting any number out of this layer
-- ------------------------------------------------------------
-- 1. stated_tenor_months is a NEW synthetic field -- the original
--    workbook-derived schema never tracked a contractual/stated tenor
--    at issuance. Generated per product in the synthetic generator
--    (e.g. Gold loans short-tenor, LAP/HCF longer) as an illustrative
--    range, not fitted to any real IIFL deal terms. TO BE CONFIRMED
--    against real deal documentation before relying on it.
--
-- 2. investor_payout_rate = calc.deal_calculated_fields.current_roi
--    (already the authoritative "current rate" per deal from
--    02_schema_calc.sql -- reused here rather than recomputed, to keep
--    one source of truth for that number).
--
-- 3. all_in_cost_pct = investor_payout_rate + servicing_fee_pct +
--    an AMORTIZED-UPFRONT-COST component. The one-time execution costs
--    (processing/documentation/CA/legal/rating/trustee/arranger fees --
--    calc.deal_calculated_fields.total_deal_execution_expenses) are
--    spread straight-line over the deal's STATED tenor to turn a one-off
--    Rs Crore figure into an annualized percentage-point addition to the
--    coupon, similar in spirit to an APR/effective-rate calculation.
--    TO BE CONFIRMED / KNOWN SIMPLIFICATION: this amortizes over the
--    STATED tenor, not the pool's actual expected life (WAL) -- WAL is
--    intentionally not computed in SQL (see sql/06_schema_delinquency.sql
--    assumption #6 and python/risk_metrics.py), so this view stays
--    SQL-only and simple. Since WAL is typically SHORTER than stated
--    tenor once prepayment is considered, amortizing over stated tenor
--    UNDERSTATES the true effective all-in cost versus a WAL-amortized
--    figure. The "Funding & ALM" dashboard page shows the WAL-vs-tenor
--    comparison alongside this for that reason -- read them together,
--    not this view in isolation.
--
-- 4. Trustee/rating/arranger/etc fees are treated as one-time (matches
--    the existing calc.deal_calculated_fields treatment from
--    02_schema_calc.sql) even though trustee_fees_pa's own column name
--    suggests a recurring per-annum charge -- a naming quirk inherited
--    from the original workbook, not re-litigated here. TO BE CONFIRMED
--    if trustee fees are actually recurring annually in the real deals.
--
-- 5. KNOWN DATASET LIMITATION (not a bug in this view, a gap in the
--    generator): the synthetic generator's monthly scheduled
--    amortization rate is drawn independently of a deal's product or
--    stated_tenor_months. The WAL-vs-stated-tenor ALM check on the
--    "Funding & ALM" dashboard page will therefore flag most
--    longer-stated-tenor deals (HCF, LAP) SYSTEMATICALLY, because their
--    WAL is driven by a tenor-agnostic amortization rate while their
--    stated tenor is drawn from a longer product-specific range -- not
--    because those specific deals have a genuine, occasional ALM
--    problem. A more realistic generator would tie the scheduled
--    amortization rate to stated_tenor_months so WAL and tenor broadly
--    agree BEFORE prepayment is layered on, so that a flagged mismatch
--    would actually mean something. Left as-is here rather than
--    reworking the amortization model this late in the build --
--    documented explicitly instead of silently smoothed over. See
--    README's "how I'd extend this" section.
-- ============================================================

ALTER TABLE raw.deals
    ADD COLUMN IF NOT EXISTS stated_tenor_months INTEGER;

CREATE OR REPLACE VIEW calc.deal_funding_cost AS
SELECT
    d.deal_id, d.deal_name, d.investor_name, d.instrument_type, d.underlying,
    d.status, d.stated_tenor_months,
    dcf.current_roi AS investor_payout_rate,
    d.servicing_fee_pct,
    dcf.total_deal_execution_expenses,
    d.amount_securitised_deal_date,
    CASE WHEN d.amount_securitised_deal_date > 0
         THEN dcf.total_deal_execution_expenses / d.amount_securitised_deal_date * 100
    END AS upfront_cost_pct,
    CASE WHEN d.amount_securitised_deal_date > 0 AND d.stated_tenor_months > 0
         THEN (dcf.total_deal_execution_expenses / d.amount_securitised_deal_date * 100)
              / (d.stated_tenor_months / 12.0)
    END AS upfront_cost_amortized_pct,
    dcf.current_roi + COALESCE(d.servicing_fee_pct, 0) +
        COALESCE(
            CASE WHEN d.amount_securitised_deal_date > 0 AND d.stated_tenor_months > 0
                 THEN (dcf.total_deal_execution_expenses / d.amount_securitised_deal_date * 100)
                      / (d.stated_tenor_months / 12.0)
            END, 0)
        AS all_in_cost_pct
FROM raw.deals d
JOIN calc.deal_calculated_fields dcf ON dcf.deal_id = d.deal_id;
