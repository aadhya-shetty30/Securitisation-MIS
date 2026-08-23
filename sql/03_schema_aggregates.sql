-- ============================================================
-- AGGREGATION LAYER
-- Replaces: Rate Sheet, MOM Payout Summary, Management Summary,
-- Executive Review's KPI/table logic. These views feed the
-- Streamlit dashboard directly -- the dashboard should not
-- contain business logic, only display logic.
-- ============================================================

-- ------------------------------------------------------------
-- rate_projection
-- Replaces "Rate Sheet". Forward ROI/MCLR projection per deal per month.
-- Original monthly MCLR formula was incomplete/ambiguous in the source
-- workbook ("=IF(AND($L4="variable"*(GI$2>=$T4),$J4,$Q4)") and was
-- explicitly NOT to be guessed. We implement the CONFIRMED logic only:
--   - Fixed-rate deals: MCLR/ROI held constant at deal-date values
--   - Variable-rate deals: ROI = MCLR + Spread, using the deal's
--     latest known MCLR from monthly_mis
-- Reset-date-driven MCLR changes for variable deals are marked
-- TO BE CONFIRMED and NOT implemented until the real reset logic
-- is confirmed with the team.
-- ------------------------------------------------------------
CREATE VIEW calc.rate_projection AS
SELECT
    d.deal_id,
    d.deal_name,
    d.mclr_linked_or_fixed,
    d.spread,
    d.deal_roi AS roi_at_deal_time,
    mm.month,
    mm.mclr AS mclr_actual,
    mm.roi AS roi_actual,
    CASE
        WHEN d.mclr_linked_or_fixed = 'Fixed' THEN d.deal_roi
        ELSE mm.mclr + d.spread   -- TO BE CONFIRMED: reset-date timing not implemented
    END AS roi_computed_check,
    mm.closing_pos_investor_share AS estimated_outstanding
FROM raw.deals d
JOIN raw.monthly_mis mm ON mm.deal_id = d.deal_id
ORDER BY d.deal_id, mm.month;


-- ------------------------------------------------------------
-- mom_payout_summary
-- Replaces "MOM Payout Summary": quarter-wise incremental assignment,
-- weighted ROI, payout, instrument (PTC/DA) and product (Gold/SME/etc) splits.
-- ------------------------------------------------------------
CREATE VIEW calc.quarterly_assignment AS
SELECT
    d.period AS quarter,
    SUM(d.amount_securitised_deal_date) AS incremental_assignment,
    -- weighted ROI = sum(amount * deal_roi) / sum(amount), never simple average
    CASE WHEN SUM(d.amount_securitised_deal_date) = 0 THEN NULL
         ELSE SUM(d.amount_securitised_deal_date * d.deal_roi) / SUM(d.amount_securitised_deal_date)
    END AS weighted_assignment_roi
FROM raw.deals d
GROUP BY d.period;

CREATE VIEW calc.monthly_payout_summary AS
SELECT
    mm.month,
    d.instrument_type,
    d.underlying AS product,
    -- Payout is only summed when every component is present; otherwise flagged.
    -- Mirrors the original's explicit "Incomplete" vs computed distinction.
    COUNT(*) FILTER (WHERE mm.data_status != 'Available') AS incomplete_rows,
    SUM(mm.total_bank_payout) FILTER (WHERE mm.data_status = 'Available') AS total_payout,
    CASE
        WHEN SUM(mm.closing_pos_investor_share) FILTER (WHERE mm.data_status = 'Available') = 0
             OR SUM(mm.closing_pos_investor_share) FILTER (WHERE mm.data_status = 'Available') IS NULL
        THEN NULL
        ELSE SUM(mm.total_bank_payout * mm.roi) FILTER (WHERE mm.data_status = 'Available')
             / SUM(mm.closing_pos_investor_share) FILTER (WHERE mm.data_status = 'Available')
    END AS payout_weighted_roi
FROM raw.monthly_mis mm
JOIN raw.deals d ON d.deal_id = mm.deal_id
GROUP BY mm.month, d.instrument_type, d.underlying;


-- ------------------------------------------------------------
-- investor_summary
-- Replaces "Management Summary": investor-wise KPIs for a given month.
-- The Streamlit app filters this view by investor + month at query time
-- instead of a spreadsheet CHOOSE/MATCH selector.
-- ------------------------------------------------------------
CREATE VIEW calc.investor_summary AS
SELECT
    d.investor_name,
    mm.month,
    COUNT(DISTINCT d.deal_id) AS total_deals,
    COUNT(DISTINCT d.deal_id) FILTER (WHERE d.status = 'Active') AS active_deals,
    SUM(d.amount_securitised_deal_date) AS total_deal_amount,
    SUM(mm.closing_pos_investor_share) FILTER (WHERE mm.data_status = 'Available') AS investor_pos,
    SUM(mm.total_bank_payout) FILTER (WHERE mm.data_status = 'Available') AS payout,
    SUM(mm.npa_amount) FILTER (WHERE mm.data_status = 'Available') AS npa
FROM raw.deals d
JOIN raw.monthly_mis mm ON mm.deal_id = d.deal_id
GROUP BY d.investor_name, mm.month;


-- ------------------------------------------------------------
-- executive_review_fy / executive_review_qoq
-- Replaces Executive Review Table 1 (FY-wise) and Table 2 (QoQ)
-- ------------------------------------------------------------
CREATE VIEW calc.executive_review_fy AS
SELECT
    d.fy,
    COUNT(DISTINCT d.deal_id) AS deals,
    COUNT(DISTINCT d.deal_id) FILTER (WHERE d.status = 'Active') AS active,
    COUNT(DISTINCT d.deal_id) FILTER (WHERE d.status = 'Closed') AS closed,
    SUM(d.amount_securitised_deal_date) AS deal_value,
    AVG(d.amount_securitised_deal_date) AS avg_deal_size,
    MIN(d.borrowing_date) AS first_deal,
    MAX(d.borrowing_date) AS latest_deal,
    SUM(d.amount_securitised_deal_date) FILTER (WHERE d.instrument_type = 'DA') AS da_deal_value,
    SUM(d.amount_securitised_deal_date) FILTER (WHERE d.instrument_type = 'PTC') AS secu_deal_value
FROM raw.deals d
GROUP BY d.fy;

CREATE VIEW calc.executive_review_qoq AS
SELECT
    d.period AS quarter,
    COUNT(DISTINCT d.deal_id) AS deals,
    COUNT(DISTINCT d.deal_id) FILTER (WHERE d.status = 'Active') AS active,
    COUNT(DISTINCT d.deal_id) FILTER (WHERE d.status = 'Closed') AS closed,
    SUM(d.amount_securitised_deal_date) AS deal_value
FROM raw.deals d
GROUP BY d.period;
-- QoQ deltas (value change, growth %, cumulative value, value share) are computed
-- in the Python layer using window functions (LAG/cumulative SUM) over this view --
-- simpler to express and test in pandas than nested SQL window logic for a first pass.
