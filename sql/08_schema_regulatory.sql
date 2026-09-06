-- ============================================================
-- PHASE 5 -- REGULATORY MARKERS
-- Adds MHP (Minimum Holding Period) seasoning data and a true-sale
-- assumption note to raw.deals, plus calc.* views for MHP compliance
-- and a combined regulatory compliance summary. MRR itself was already
-- added in Phase 1 (raw.deals.mrr_percent, calc.mrr_compliance in
-- sql/05_schema_waterfall.sql) -- not duplicated here.
--
-- Run this AFTER 01-07_schema*.sql have already been applied.
-- Safe to re-run: ALTER ... ADD COLUMN IF NOT EXISTS and
-- CREATE OR REPLACE VIEW are used throughout.
--
-- ------------------------------------------------------------
-- ASSUMPTIONS -- read before trusting any number out of this layer.
-- This is explicitly a SIMPLE compliance summary, not a rules engine --
-- per the task's own framing, it should surface flags for a human to
-- review, not adjudicate real regulatory compliance.
-- ------------------------------------------------------------
-- 1. MRR compliance threshold is DELIBERATELY NOT hardcoded here (unlike
--    the fixed-5% calc.mrr_compliance view from Phase 1, which stays as
--    a quick reference). The "Regulatory Compliance" dashboard page
--    applies a user-adjustable threshold directly to raw.deals.mrr_percent
--    instead, per this phase's "configurable threshold" requirement.
--
-- 2. MHP (Minimum Holding Period): RBI's actual MHP matrix (Master
--    Direction on Securitisation of Standard Assets) varies by BOTH
--    original loan tenor AND repayment frequency (e.g. weekly/monthly
--    vs less frequent). This schema tracks neither repayment frequency
--    nor loan-level tenor (only stated_tenor_months at the DEAL level
--    from Phase 4). required_mhp_months below is a DELIBERATE
--    SIMPLIFICATION of the real matrix: <=24 months stated tenor -> 3
--    months MHP, >24 months -> 6 months. TO BE CONFIRMED against the
--    current RBI Master Direction before using this for anything beyond
--    a demo -- this is illustrative, not a verbatim reproduction of the
--    regulation.
--
-- 3. underlying_seasoning_months (how much repayment history the pool
--    had at deal execution) is a NEW synthetic field -- not tracked
--    anywhere in the original workbook-derived schema. Generated with a
--    deliberate ~15% non-compliant slice (seasoning below the required
--    MHP) so the compliance check has something real to flag, mirroring
--    how monthly_mis's data-quality gaps were deliberately injected
--    rather than everything being clean by construction.
--
-- 4. true_sale_criteria_met / true_sale_assumption_note are NOT a legal
--    determination -- they record what this synthetic project ASSUMES
--    about true-sale status (no recourse to originator beyond stated
--    credit enhancement, SPV/assignee holds legal or beneficial title,
--    etc), for a small number of deals deliberately marked "not
--    independently verified" to demonstrate the flag. A real true-sale
--    opinion requires actual legal review of the transfer documents,
--    which is obviously out of scope for a synthetic student project.
-- ============================================================

ALTER TABLE raw.deals
    ADD COLUMN IF NOT EXISTS underlying_seasoning_months INTEGER,
    ADD COLUMN IF NOT EXISTS true_sale_criteria_met BOOLEAN,
    ADD COLUMN IF NOT EXISTS true_sale_assumption_note TEXT;

CREATE OR REPLACE VIEW calc.mhp_compliance AS
SELECT
    d.deal_id, d.deal_name, d.investor_name, d.instrument_type, d.underlying,
    d.stated_tenor_months, d.underlying_seasoning_months,
    CASE
        WHEN d.stated_tenor_months IS NULL THEN NULL
        WHEN d.stated_tenor_months <= 24 THEN 3
        ELSE 6
    END AS required_mhp_months,
    CASE
        WHEN d.underlying_seasoning_months IS NULL OR d.stated_tenor_months IS NULL THEN 'Unknown'
        WHEN d.underlying_seasoning_months >=
             (CASE WHEN d.stated_tenor_months <= 24 THEN 3 ELSE 6 END)
        THEN 'Compliant'
        ELSE 'Non-Compliant'
    END AS mhp_compliance_status
FROM raw.deals d;

-- Combined per-deal regulatory summary -- surfaced as a simple table on
-- the dashboard, not a rules engine. MRR compliance status is left for
-- the dashboard to compute against its adjustable threshold (see
-- assumption #1); mrr_percent is passed through raw here for that.
CREATE OR REPLACE VIEW calc.regulatory_compliance_summary AS
SELECT
    d.deal_id, d.deal_name, d.investor_name, d.instrument_type, d.underlying, d.status,
    d.mrr_percent,
    m.required_mhp_months, m.mhp_compliance_status,
    d.true_sale_criteria_met, d.true_sale_assumption_note
FROM raw.deals d
JOIN calc.mhp_compliance m ON m.deal_id = d.deal_id;
