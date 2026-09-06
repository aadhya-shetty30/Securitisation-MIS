"""
Step 6 -- Load synthetic CSVs into a real PostgreSQL instance.

Usage:
    1. Have a Postgres instance reachable (local Docker, or a free
       hosted instance e.g. Neon / Supabase / Railway).
    2. Set the connection string as an env var:
           export NBFC_MIS_DB_URL="postgresql://user:pass@host:port/dbname"
    3. pip install psycopg2-binary --break-system-packages
    4. python load_to_postgres.py

This script runs SCHEMA-FIRST, THEN DATA:
    1. 01_schema_raw.sql              -- raw tables (no data needed yet)
    2. 05_schema_waterfall.sql        -- tranche/CE/MRR columns on raw.deals,
                                          raw.monthly_waterfall table + its views
    3. 06_schema_delinquency.sql      -- scheduled_principal_payout column on
                                          raw.monthly_mis, raw.monthly_delinquency
                                          table + its views
    4. 02_schema_calc.sql             -- calc.* views (only need table/column
                                          structure, not data)
    5. 03_schema_aggregates.sql       -- calc.* aggregate views
    6. 07_schema_funding.sql          -- stated_tenor_months column on raw.deals
                                          + calc.deal_funding_cost (depends on
                                          calc.deal_calculated_fields from step 4,
                                          which is why it runs after 02, not with 05/06)
    7. 08_schema_regulatory.sql       -- MHP/true-sale columns on raw.deals +
                                          calc.mhp_compliance / calc.regulatory_compliance_summary
    8. CSV loads via COPY, in dependency order: master_code -> deals ->
       monthly_mis -> monthly_waterfall -> monthly_delinquency (fast, and
       respects the FK/CHECK/UNIQUE constraints already defined in the
       schema -- any bad row fails loudly here rather than silently
       corrupting the dataset)
    9. a handful of sanity queries so you can see it worked

All CREATE VIEW statements only need the referenced tables/columns to
exist in the catalog, not be populated -- so building every table +
view first, then loading data once, is both simpler and safer than
interleaving (no view definition can ever reference a not-yet-existing
column added by a later step).

NOTE for 04_schema_auth.sql (RBAC/audit): not run by this script -- it's
independent of the raw/calc data layer and is applied separately (see
README). If you're loading into a brand-new database, run it once
yourself alongside this script.
"""

import os
import sys
import psycopg2

DB_URL = os.environ.get("NBFC_MIS_DB_URL")
if not DB_URL:
    print("Set NBFC_MIS_DB_URL first, e.g.:")
    print('  export NBFC_MIS_DB_URL="postgresql://user:pass@localhost:5432/nbfc_mis"')
    sys.exit(1)

SQL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql")
CSV_DIR = os.path.dirname(__file__)

SCHEMA_FILES = [
    "01_schema_raw.sql",
    "05_schema_waterfall.sql",
    "06_schema_delinquency.sql",
    "02_schema_calc.sql",
    "03_schema_aggregates.sql",
    "07_schema_funding.sql",
    "08_schema_regulatory.sql",
]


def run_sql_file(cur, path):
    with open(path) as f:
        cur.execute(f.read())
    print(f"  ran {os.path.basename(path)}")


def load_csv(cur, table, csv_path, columns):
    with open(csv_path) as f:
        cur.copy_expert(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')",
            f,
        )
    print(f"  loaded {csv_path} -> {table}")


def main():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Building schema (tables + views, no data yet)...")
        for filename in SCHEMA_FILES:
            run_sql_file(cur, os.path.join(SQL_DIR, filename))

        print("Loading data...")
        load_csv(cur, "raw.master_code", os.path.join(CSV_DIR, "master_code.csv"),
                  ["company", "instrument", "deal_name", "code", "status"])

        deals_cols = [
            "deal_id", "sub_code", "deal_name", "status", "fy", "period", "company",
            "instrument_type", "underlying", "psl_npsl", "investor_name", "trustee_ar",
            "trustee", "ratio", "borrowing_date", "payout_date", "mclr_linked_or_fixed",
            "mclr_on_deal_date", "spread", "deal_roi", "servicing_fee_pct", "net_roi",
            "no_of_pool_contracts", "amount_securitised_deal_date",
            "purchase_consideration_deal_date", "rating", "loss_estimation_rating",
            "first_loss_facility_pct", "overcollateral_pct", "tranche",
            "credit_enhancement_type", "credit_enhancement_initial_amount", "mrr_percent",
            "stated_tenor_months", "underlying_seasoning_months",
            "true_sale_criteria_met", "true_sale_assumption_note",
            "processing_fees_paid", "documentation_charges", "external_ca_fees",
            "legal_documentation_fees", "rating_fees", "trustee_fees_pa", "arranger_fees",
        ]
        load_csv(cur, "raw.deals", os.path.join(CSV_DIR, "deals.csv"), deals_cols)

        mis_cols = [
            "deal_id", "month", "total_pool_outstanding", "closing_pos_investor_share",
            "total_bank_payout", "total_principal_payout", "scheduled_principal_payout",
            "npa_amount", "mclr", "roi", "investor_share_x_roi", "data_status",
        ]
        load_csv(cur, "raw.monthly_mis", os.path.join(CSV_DIR, "monthly_mis.csv"), mis_cols)

        waterfall_cols = [
            "deal_id", "month", "collections_available", "servicing_fee_paid",
            "senior_interest_paid", "senior_principal_paid", "subordinate_interest_paid",
            "subordinate_principal_paid", "excess_spread_to_originator",
            "ce_opening_balance", "ce_drawn_this_month", "ce_closing_balance",
            "ce_cover_ratio",
        ]
        load_csv(cur, "raw.monthly_waterfall", os.path.join(CSV_DIR, "monthly_waterfall.csv"),
                  waterfall_cols)

        delinquency_cols = [
            "deal_id", "month", "pos_0dpd", "pos_1_30dpd", "pos_31_60dpd",
            "pos_61_90dpd", "pos_90plus_dpd", "cumulative_default_amount",
        ]
        load_csv(cur, "raw.monthly_delinquency", os.path.join(CSV_DIR, "monthly_delinquency.csv"),
                  delinquency_cols)

        conn.commit()
        print("\nAll committed successfully.\n")

        print("Sanity checks:")
        cur.execute("SELECT count(*) FROM raw.deals")
        print(f"  raw.deals row count: {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM raw.monthly_mis")
        print(f"  raw.monthly_mis row count: {cur.fetchone()[0]}")
        cur.execute("SELECT investor_name, total_deals, active_deals FROM calc.investor_summary "
                     "WHERE month = (SELECT max(month) FROM raw.monthly_mis) ORDER BY total_deals DESC LIMIT 5")
        print("  Top 5 investors by deal count (calc.investor_summary):")
        for row in cur.fetchall():
            print(f"    {row}")

    except Exception as e:
        conn.rollback()
        print(f"\nERROR -- rolled back. {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
