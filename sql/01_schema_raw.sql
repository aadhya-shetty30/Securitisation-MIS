-- ============================================================
-- NBFC PTC/DA SECURITISATION MIS -- RAW / INPUT LAYER
-- Step 4 deliverable. Mirrors the manually-entered portion of
-- the existing workbook. These three tables are the ONLY tables
-- populated by manual/synthetic data entry -- everything else
-- in the project is derived from them (see 02_schema_calc.sql
-- and 03_schema_aggregates.sql).
-- ============================================================

CREATE SCHEMA IF NOT EXISTS raw;

-- ------------------------------------------------------------
-- 1. master_code
-- Source: "Master Code" sheet (manual, no formulas).
-- Purpose: canonical dictionary. A deal must be registered here
-- before it can exist in raw.deals -- mirrors the real workbook,
-- where Master Code is the reference table everything else keys off.
-- ------------------------------------------------------------
CREATE TABLE raw.master_code (
    sr_no       SERIAL PRIMARY KEY,
    company     TEXT NOT NULL,
    instrument  TEXT NOT NULL CHECK (instrument IN ('PTC', 'DA')),
    deal_name   TEXT NOT NULL UNIQUE,
    code        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL CHECK (status IN ('Active', 'Closed'))
);

-- ------------------------------------------------------------
-- 2. deals
-- Source: "IIFL_Static Data 2026" sheet -- MANUAL columns only.
-- The 5 columns that were formulas in the original workbook
-- (Current ROI, Amount Securitised, Purchase Consideration,
--  O/s June 2026, Total Deal Execution Expenses) are DELIBERATELY
-- excluded -- they belong in the calculation layer because in
-- this redesign they are derived on read, never stored.
-- One row per deal. Static at the deal level: nothing here
-- changes month to month.
-- ------------------------------------------------------------
CREATE TABLE raw.deals (
    -- Identity
    deal_id                  TEXT PRIMARY KEY,           -- Code (Static Data col C)
    sub_code                 TEXT,                        -- Sub code for Liquid PTC (col D)
    deal_name                TEXT NOT NULL UNIQUE,
    status                   TEXT NOT NULL CHECK (status IN ('Active', 'Closed')),

    -- Classification
    fy                       TEXT NOT NULL,               -- e.g. 'FY26'
    period                   TEXT NOT NULL,               -- Quarter tag used by MOM Payout Summary, e.g. 'FY26 Q1'
    company                  TEXT NOT NULL,
    instrument_type          TEXT NOT NULL CHECK (instrument_type IN ('PTC', 'DA')),
    underlying                TEXT NOT NULL,               -- product category: Gold, SME, MFI, LAP, BL, HCF, etc.
    psl_npsl                  TEXT CHECK (psl_npsl IN ('PSL', 'NPSL')),

    -- Counterparties
    investor_name              TEXT NOT NULL,
    trustee_ar                 TEXT,
    trustee                    TEXT,

    -- Deal terms
    ratio                      NUMERIC(10,4),
    borrowing_date             DATE NOT NULL,
    payout_date                DATE,                       -- drives monthly payout day-of-month
    mclr_linked_or_fixed       TEXT CHECK (mclr_linked_or_fixed IN ('MCLR Linked', 'Fixed')),
    mclr_on_deal_date          NUMERIC(6,4),
    spread                     NUMERIC(6,4),
    deal_roi                   NUMERIC(6,4) NOT NULL,      -- ROI fixed at deal inception
    servicing_fee_pct          NUMERIC(6,4),
    net_roi                    NUMERIC(6,4),

    -- Size
    -- TO BE CONFIRMED: original workbook derived these two via VLOOKUP
    -- against a deal-level block inside "MIS DATA Dynamic" rather than
    -- storing them directly in Static Data. Modeled here as raw deal-level
    -- attributes captured at origination (a defensible simplification
    -- for the synthetic project, since they don't change month to month) --
    -- validate against the real source of truth during the internship.
    no_of_pool_contracts               INTEGER,
    amount_securitised_deal_date       NUMERIC(14,2) NOT NULL,  -- Rs. Crs
    purchase_consideration_deal_date   NUMERIC(14,2),           -- Rs. Crs

    -- Credit quality
    rating                     TEXT,
    loss_estimation_rating     TEXT,
    first_loss_facility_pct    NUMERIC(6,4),
    overcollateral_pct         NUMERIC(6,4),

    -- Execution costs (7 components -> summed in calc layer)
    processing_fees_paid       NUMERIC(12,2),
    documentation_charges      NUMERIC(12,2),
    external_ca_fees           NUMERIC(12,2),
    legal_documentation_fees   NUMERIC(12,2),
    rating_fees                NUMERIC(12,2),
    trustee_fees_pa             NUMERIC(12,2),
    arranger_fees               NUMERIC(12,2),

    CONSTRAINT fk_deals_master_code FOREIGN KEY (deal_name)
        REFERENCES raw.master_code (deal_name)
);

CREATE INDEX idx_deals_investor ON raw.deals (investor_name);
CREATE INDEX idx_deals_fy_period ON raw.deals (fy, period);
CREATE INDEX idx_deals_status ON raw.deals (status);

-- ------------------------------------------------------------
-- 3. monthly_mis
-- Source: "MIS DATA Dynamic" sheet -- replaces the WIDE layout
-- (Mar-26 POS | Apr-26 POS | May-26 POS | ...) with a LONG table:
-- one row per deal per month. This is the single biggest
-- structural fix versus the original workbook -- adding a new
-- month is an INSERT, not a new column pair plus new formulas
-- rewritten across four other sheets.
-- ------------------------------------------------------------
CREATE TABLE raw.monthly_mis (
    id                          BIGSERIAL PRIMARY KEY,
    deal_id                     TEXT NOT NULL REFERENCES raw.deals (deal_id),
    month                       DATE NOT NULL,              -- normalized to first-of-month, e.g. 2026-07-01

    total_pool_outstanding      NUMERIC(14,2),
    closing_pos_investor_share  NUMERIC(14,2),               -- "Investor POS"
    total_bank_payout           NUMERIC(14,2),
    total_principal_payout      NUMERIC(14,2),
    npa_amount                  NUMERIC(14,2),                -- "NPA (100%)"
    mclr                        NUMERIC(6,4),
    roi                         NUMERIC(6,4),
    investor_share_x_roi        NUMERIC(14,2),

    -- Mirrors the real workbook's explicit distinction between
    -- "unavailable" and "zero" (e.g. Apr-25 to Feb-26 payout source
    -- columns were deleted -> unavailable, NOT zero). Never default
    -- missing data to zero in the calculation layer -- check this
    -- column instead.
    data_status                 TEXT NOT NULL DEFAULT 'Available'
        CHECK (data_status IN ('Available', 'Zero', 'Missing', 'Incomplete', 'Not Applicable', 'Reconciliation Required')),

    UNIQUE (deal_id, month)
);

CREATE INDEX idx_monthly_mis_deal_month ON raw.monthly_mis (deal_id, month);
CREATE INDEX idx_monthly_mis_month ON raw.monthly_mis (month);
