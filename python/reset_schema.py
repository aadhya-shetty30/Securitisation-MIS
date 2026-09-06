"""
One-off utility: drops the raw and calc schemas so load_to_postgres.py
can be re-run from scratch against a database that already has an
older version of the schema loaded (01_schema_raw.sql uses CREATE
TABLE, not CREATE TABLE IF NOT EXISTS, so it fails against existing
tables).

Deliberately does NOT touch the auth schema -- your users/logins and
audit log survive a reset, since they're independent of the raw/calc
data layer (see 04_schema_auth.sql).

Usage:
    export NBFC_MIS_DB_URL="postgresql://...neon connection string..."
    python reset_schema.py
Prompts for a typed "yes" confirmation before dropping anything.
"""

import os
import sys
import psycopg2

DB_URL = os.environ.get("NBFC_MIS_DB_URL")
if not DB_URL:
    print("Set NBFC_MIS_DB_URL first.")
    sys.exit(1)

print("This will DROP SCHEMA raw CASCADE and DROP SCHEMA calc CASCADE")
print(f"on: {DB_URL.split('@')[-1] if '@' in DB_URL else DB_URL}")
print("(auth schema -- your users/logins/audit log -- is left untouched)")
confirm = input('Type "yes" to proceed: ').strip().lower()
if confirm != "yes":
    print("Aborted.")
    sys.exit(0)

conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()
cur.execute("DROP SCHEMA IF EXISTS raw CASCADE;")
cur.execute("DROP SCHEMA IF EXISTS calc CASCADE;")
cur.close()
conn.close()

print("Done. raw and calc schemas dropped -- run load_to_postgres.py next.")
