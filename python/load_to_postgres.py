"""
Step 6 -- Load synthetic CSVs into a real PostgreSQL instance.

Usage:
    1. Have a Postgres instance reachable (local Docker, or a free
       hosted instance e.g. Neon / Supabase / Railway).
    2. Set the connection string as an env var:
           export NBFC_MIS_DB_URL="postgresql://user:pass@host:port/dbname"
    3. pip install psycopg2-binary --break-system-packages
    4. python load_to_postgres.py

This script:
    - runs 01_schema_raw.sql to (re)create schema + tables
    - loads master_code.csv, deals.csv, monthly_mis.csv via COPY
      (fast, and respects the FK/CHECK/UNIQUE constraints already
      defined in the schema -- any bad row fails loudly here rather
      than silently corrupting the dataset)
    - runs 02_schema_calc.sql and 03_schema_aggregates.sql to build
      the calculation and aggregation views on top
    - runs a handful of sanity queries so you can see it worked
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
        print("Creating schema...")
        run_sql_file(cur, os.path.join(SQL_DIR, "01_schema_raw.sql"))

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
            "first_loss_facility_pct", "overcollateral_pct", "processing_fees_paid",
            "documentation_charges", "external_ca_fees", "legal_documentation_fees",
            "rating_fees", "trustee_fees_pa", "arranger_fees",
        ]
        load_csv(cur, "raw.deals", os.path.join(CSV_DIR, "deals.csv"), deals_cols)

        mis_cols = [
            "deal_id", "month", "total_pool_outstanding", "closing_pos_investor_share",
            "total_bank_payout", "total_principal_payout", "npa_amount", "mclr", "roi",
            "investor_share_x_roi", "data_status",
        ]
        load_csv(cur, "raw.monthly_mis", os.path.join(CSV_DIR, "monthly_mis.csv"), mis_cols)

        print("Building calculation + aggregation views...")
        run_sql_file(cur, os.path.join(SQL_DIR, "02_schema_calc.sql"))
        run_sql_file(cur, os.path.join(SQL_DIR, "03_schema_aggregates.sql"))

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
