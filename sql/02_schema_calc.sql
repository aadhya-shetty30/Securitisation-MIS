-- ============================================================
-- CALCULATION LAYER
-- Replaces the XLOOKUP/VLOOKUP/CHOOSE+MATCH sprawl in the
-- original workbook (Static Data's 5 formula columns, Rate Sheet,
-- MOM Payout Summary, Dashboard Data) with SQL views computed
-- once, off the two raw tables (deals, monthly_mis).
--
-- Design choice: these are VIEWS, not materialized tables, so
-- they always reflect the latest raw data with zero manual
-- refresh step — this is the direct fix for "add a month ->
-- touch 4 sheets by hand."
-- (Can be swapped to MATERIALIZED VIEW + REFRESH if the dataset
-- grows large enough that recompute-on-query is too slow —
-- flagged as a scaling note, not needed at this data volume.)
-- ============================================================

CREATE SCHEMA IF NOT EXISTS calc;

-- ------------------------------------------------------------
-- deal_calculated_fields
-- Replaces the 5 formula columns from IIFL_Static Data 2026:
--   Current ROI, Amount Securitised as on Deal Date,
--   Purchase Consideration O/s as on Deal Date, O/s June 2026 (-> latest month),
--   First Loss Facility Amount, Overcollateral Amount, Total Deal Execution Expenses
-- ------------------------------------------------------------
CREATE VIEW calc.deal_calculated_fields AS
WITH latest_mis AS (
    -- one row per deal: its most recent monthly_mis record
    SELECT DISTINCT ON (deal_id)
        deal_id, month, closing_pos_investor_share, roi, total_bank_payout, data_status
    FROM raw.monthly_mis
    ORDER BY deal_id, month DESC
)
SELECT
    d.deal_id,
    -- Current ROI: Closed deals freeze at Deal ROI; open deals take latest MIS ROI,
    -- falling back to Deal ROI if no MIS record exists yet (mirrors original fallback logic)
    CASE
        WHEN d.status = 'Closed' THEN d.deal_roi
        ELSE COALESCE(lm.roi, d.deal_roi)
    END AS current_roi,

    -- Amount Securitised as on Deal Date: in the redesign this is a raw deal attribute
    -- captured at origination, not a lookup (see TO BE CONFIRMED note below) --
    -- exposed here too so downstream code can read all deal-level KPIs from one view
    d.amount_securitised_deal_date,
    d.purchase_consideration_deal_date,

    -- First Loss Facility / Overcollateral amounts, now computable directly
    d.first_loss_facility_pct * d.amount_securitised_deal_date AS first_loss_facility_amount,
    d.overcollateral_pct * d.amount_securitised_deal_date AS overcollateral_amount,

    -- Latest outstanding (generalizes "O/s June 2026" to "O/s as of latest available month")
    CASE WHEN d.status = 'Closed' THEN 0 ELSE lm.closing_pos_investor_share END AS latest_outstanding,
    lm.month AS latest_outstanding_month,
    lm.data_status AS latest_outstanding_status,

    -- Total Deal Execution Expenses: sum of the 7 expense components, NULL (not 0) if all blank
    CASE
        WHEN d.processing_fees_paid IS NULL AND d.documentation_charges IS NULL
             AND d.external_ca_fees IS NULL AND d.legal_documentation_fees IS NULL
             AND d.rating_fees IS NULL AND d.trustee_fees_pa IS NULL AND d.arranger_fees IS NULL
        THEN NULL
        ELSE COALESCE(d.processing_fees_paid, 0) + COALESCE(d.documentation_charges, 0)
             + COALESCE(d.external_ca_fees, 0) + COALESCE(d.legal_documentation_fees, 0)
             + COALESCE(d.rating_fees, 0) + COALESCE(d.trustee_fees_pa, 0)
             + COALESCE(d.arranger_fees, 0)
    END AS total_deal_execution_expenses

FROM raw.deals d
LEFT JOIN latest_mis lm ON lm.deal_id = d.deal_id;

-- NOTE (TO BE CONFIRMED): In the original workbook, "Amount Securitised as on Deal Date"
-- and "Purchase Consideration O/s as on Deal Date" were pulled via VLOOKUP from
-- MIS DATA Dynamic (a deal-level block, not the monthly block). In this redesign we
-- treat Amount Securitised as a raw deal-level attribute captured at deal origination
-- (added to raw.deals as amount_securitised_deal_date and purchase_consideration_deal_date
-- in the synthetic data generator) rather than a monthly lookup, since it does not change
-- month to month. This is a deliberate simplification for the synthetic project and should
-- be validated against how IIFL's real source-of-truth actually stores it.


-- ------------------------------------------------------------
-- data_quality_flags
-- Deal x Month completeness check. Distinguishes "no row exists yet"
-- (Missing) from "row exists but data_status says otherwise."
-- ------------------------------------------------------------
CREATE VIEW calc.data_quality_flags AS
SELECT
    d.deal_id,
    m.month_series AS month,
    COALESCE(mm.data_status, 'Missing') AS data_status,
    (mm.id IS NULL) AS row_absent
FROM raw.deals d
CROSS JOIN (
    SELECT generate_series(
        (SELECT MIN(month) FROM raw.monthly_mis),
        (SELECT MAX(month) FROM raw.monthly_mis),
        '1 month'::interval
    )::date AS month_series
) m
LEFT JOIN raw.monthly_mis mm ON mm.deal_id = d.deal_id AND mm.month = m.month_series;
