"""
Create or update a user account for the dashboard (any of the 3 roles).

Usage:
    export NBFC_MIS_DB_URL="postgresql://...neon connection string..."
    python seed_admin.py

Prompts for username, full name, role, and password (hidden via getpass).
Hashes the password with bcrypt before it touches the database.

Roles:
    viewer   -- read-only (rarely needed as an explicit login; the
                dashboard's 3 read pages are open to anyone anyway)
    uploader -- can upload/edit monthly MIS data
    admin    -- can also create/deactivate users and change roles
"""

import os
import sys
import getpass
import bcrypt
import psycopg2

DB_URL = os.environ.get("NBFC_MIS_DB_URL")
if not DB_URL:
    print("Set NBFC_MIS_DB_URL first.")
    sys.exit(1)

VALID_ROLES = {"viewer", "uploader", "admin"}

username = input("Username: ").strip()
full_name = input("Full name: ").strip()

role = ""
while role not in VALID_ROLES:
    role = input(f"Role ({'/'.join(sorted(VALID_ROLES))}): ").strip().lower()
    if role not in VALID_ROLES:
        print(f"  Enter one of: {', '.join(sorted(VALID_ROLES))}")

password = getpass.getpass("Password (hidden, min 8 chars): ")
password_confirm = getpass.getpass("Confirm password: ")

if password != password_confirm:
    print("Passwords do not match. Aborting.")
    sys.exit(1)

if len(password) < 8:
    print("Password should be at least 8 characters. Aborting.")
    sys.exit(1)

password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute("""
    INSERT INTO auth.users (username, password_hash, full_name, role)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (username) DO UPDATE SET
        password_hash = EXCLUDED.password_hash,
        full_name = EXCLUDED.full_name,
        role = EXCLUDED.role
""", (username, password_hash, full_name, role))

cur.execute("""
    INSERT INTO auth.audit_log (username, action, target_table, target_key, detail)
    VALUES (%s, 'USER_CREATED', 'auth.users', %s, %s)
""", ("system/seed_admin.py", username, f"role={role}, full_name={full_name}"))

conn.commit()
cur.close()
conn.close()

print(f"\nUser '{username}' created/updated with role '{role}'.")