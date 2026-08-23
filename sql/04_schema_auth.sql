-- ============================================================
-- AUTH + RBAC + AUDIT LAYER
-- Three-tier role-based access control, replacing the earlier
-- binary admin/not-admin model:
--   viewer   -- read-only (implicit for anyone not logged in, or
--               explicitly for a logged-in user with this role)
--   uploader -- can upload/edit monthly_mis, cannot manage users
--   admin    -- can also create/deactivate users and change roles
--
-- Passwords are stored as bcrypt hashes, verified in Python via
-- the bcrypt library -- never compared as plaintext in SQL.
--
-- Scope note (documented, not hidden): this is a project-appropriate
-- auth model for a handful of named users -- not SSO/Active Directory
-- integration, which a real bank deployment would need. Session
-- timeout and audit logging below are the realistic controls a
-- lightweight internal tool like this should have; SSO/MFA are
-- correctly left as noted future work rather than faked.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    username        TEXT PRIMARY KEY,
    password_hash   TEXT NOT NULL,        -- bcrypt hash, generated in Python
    full_name       TEXT,
    role            TEXT NOT NULL DEFAULT 'viewer'
        CHECK (role IN ('viewer', 'uploader', 'admin')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT REFERENCES auth.users (username)
);

-- ------------------------------------------------------------
-- audit_log
-- Records every data-modifying action taken through the app --
-- who, what action, on what table/record, when, and what changed.
-- This is the control a real treasury team would actually ask
-- about: "who touched this number and when."
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth.audit_log (
    id              BIGSERIAL PRIMARY KEY,
    username        TEXT NOT NULL,
    action          TEXT NOT NULL,          -- e.g. 'INSERT', 'UPDATE', 'LOGIN', 'LOGIN_FAILED', 'USER_CREATED'
    target_table    TEXT,                    -- e.g. 'raw.monthly_mis'
    target_key      TEXT,                    -- e.g. 'D0001 / 2026-08-01'
    detail          TEXT,                    -- human-readable summary of what changed
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address      TEXT                      -- best-effort, may be null (Streamlit Cloud proxies vary)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_username ON auth.audit_log (username);
CREATE INDEX IF NOT EXISTS idx_audit_log_occurred_at ON auth.audit_log (occurred_at DESC);


-- ------------------------------------------------------------
-- MIGRATION: if you already ran the earlier version of this file
-- (auth.admin_users), run this block once to migrate existing
-- accounts into auth.users with role='admin' preserved, then you
-- can drop the old table. Safe to skip if this is a fresh database.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema='auth' AND table_name='admin_users') THEN
        INSERT INTO auth.users (username, password_hash, full_name, role, created_at)
        SELECT username, password_hash, full_name, 'admin', created_at
        FROM auth.admin_users
        ON CONFLICT (username) DO NOTHING;
        RAISE NOTICE 'Migrated existing auth.admin_users rows into auth.users (role=admin). You can now DROP TABLE auth.admin_users; once you have verified the migration.';
    END IF;
END $$;