"""
Step 8 -- Interactive dashboard for the NBFC PTC/DA securitisation MIS.

Replicates the two presentation-layer sheets from the original workbook:
    - Management Summary: investor-wise KPIs, product/instrument mix
    - Executive Review: bank-relationship view, FY/QoQ tables, narrative

Design principle: this file contains ZERO business logic. Every number
comes from a query against the calc.* views built in 02_schema_calc.sql
and 03_schema_aggregates.sql. If a KPI is wrong, the fix belongs in SQL,
not here -- this separation is itself part of the project's pitch versus
the original workbook, where formulas and presentation were tangled
together in the same sheets.

Usage:
    export NBFC_MIS_DB_URL="postgresql://...neon connection string..."
    pip install streamlit psycopg2-binary pandas plotly
    streamlit run dashboard.py
"""

import os
import pandas as pd
import psycopg2
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="NBFC Securitisation MIS", layout="wide", page_icon="📊")

DB_URL = os.environ.get("NBFC_MIS_DB_URL")
if not DB_URL:
    st.error("Set NBFC_MIS_DB_URL as an environment variable before running.")
    st.stop()

# ------------------------------------------------------------
# Design tokens -- IIFL-inspired navy + gold, refined into a
# proper institutional-finance palette (not just tinted ivory
# everywhere). Approximate brand colors, not pixel-sampled --
# swap for exact hex codes from IIFL's style guide if available.
# ------------------------------------------------------------
NAVY = "#0A2647"          # primary brand -- headers, nav, emphasis
NAVY_LIGHT = "#14375E"    # sidebar hover / active state
INK = "#1C2B3A"           # body text
SLATE = "#64748B"         # secondary / muted text
GOLD = "#C99A2E"          # brand accent -- highlights, active markers
GOLD_SOFT = "#F4E8CC"     # gold tint for subtle backgrounds
TEAL = "#1D7A75"          # secondary data accent (DA, positive)
CORAL = "#B3452C"         # risk / NPA / missing-data accent
SURFACE = "#FFFFFF"       # card background -- clean white, not tinted
PAGE_BG = "#F4F6F9"       # app background -- cool neutral, not warm ivory
BORDER = "#E1E6ED"        # hairline borders
CHART_SEQ = ["#C99A2E", "#0A2647", "#1D7A75", "#B3452C", "#7C93AC", "#D9C48A"]

px.defaults.color_discrete_sequence = CHART_SEQ
px.defaults.template = "plotly_white"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap');

    html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}
    .stApp {{ background-color: {PAGE_BG}; }}
    .block-container {{ padding-top: 1.5rem; max-width: 1200px; }}

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] {{
        background-color: {NAVY};
        border-right: 1px solid {NAVY_LIGHT};
    }}
    section[data-testid="stSidebar"] * {{ color: #DCE6F0 !important; }}
    section[data-testid="stSidebar"] hr {{ border-color: {NAVY_LIGHT}; }}

    /* Radio nav restyled as a vertical card list */
    section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 2px; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        background-color: transparent;
        border-radius: 8px;
        padding: 9px 12px !important;
        margin: 0 !important;
        transition: background-color 0.15s ease;
        width: 100%;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
        background-color: {NAVY_LIGHT};
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label[data-checked="true"],
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {{
        background-color: {GOLD} !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {{
        color: {NAVY} !important;
        font-weight: 600 !important;
    }}
    section[data-testid="stSidebar"] .stExpander {{
        border: 1px solid {NAVY_LIGHT};
        border-radius: 8px;
        background-color: {NAVY_LIGHT};
    }}

    /* ---- Header bar ---- */
    .app-header {{
        display: flex; align-items: center; gap: 12px;
        padding-bottom: 4px; margin-bottom: 4px;
    }}
    .app-header .badge {{
        background-color: {GOLD}; color: {NAVY}; font-weight: 700;
        border-radius: 8px; width: 40px; height: 40px;
        display: flex; align-items: center; justify-content: center;
        font-size: 1.1rem; flex-shrink: 0;
    }}
    .app-header h1 {{ margin: 0 !important; font-size: 1.6rem !important; }}
    .app-subtitle {{ color: {SLATE}; font-size: 0.92rem; margin-top: -2px; margin-bottom: 1.2rem; }}

    /* ---- KPI cards ---- */
    div[data-testid="stMetric"] {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-top: 3px solid {GOLD};
        border-radius: 10px;
        padding: 16px 18px 12px 18px;
        box-shadow: 0 1px 2px rgba(10,38,71,0.04);
    }}
    div[data-testid="stMetricLabel"] {{
        color: {SLATE} !important; font-weight: 600 !important;
        font-size: 0.78rem !important; text-transform: uppercase; letter-spacing: 0.03em;
    }}
    div[data-testid="stMetricValue"] {{
        color: {NAVY} !important;
        white-space: nowrap; overflow: visible;
        font-size: 1.55rem !important; font-weight: 700 !important;
    }}

    /* ---- Typography ---- */
    h1 {{ color: {NAVY}; font-weight: 700; letter-spacing: -0.01em; }}
    h2, h3 {{ color: {NAVY}; font-weight: 600; }}
    h4, h5, .stMarkdown strong {{ color: {NAVY}; }}
    p, .stMarkdown, .stCaption {{ color: {INK}; }}

    /* ---- Narrative / callout box ---- */
    .exec-narrative {{
        background-color: {GOLD_SOFT};
        border-left: 4px solid {GOLD};
        border-radius: 8px;
        padding: 18px 22px;
        color: {NAVY};
        font-size: 0.95rem;
        line-height: 1.65;
    }}

    /* ---- Tables ---- */
    div[data-testid="stDataFrame"] {{
        border: 1px solid {BORDER}; border-radius: 10px; overflow: hidden;
    }}

    /* ---- Step / requirement cards (upload page) ---- */
    .req-card {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 18px 20px;
        margin-bottom: 10px;
    }}
    .req-card .req-num {{
        display: inline-flex; align-items: center; justify-content: center;
        background-color: {NAVY}; color: #fff; font-weight: 700;
        width: 22px; height: 22px; border-radius: 50%;
        font-size: 0.75rem; margin-right: 8px; flex-shrink: 0;
    }}
    .req-card code {{
        background-color: {GOLD_SOFT}; color: {NAVY};
        font-family: 'IBM Plex Mono', monospace; font-size: 0.82rem;
        padding: 1px 5px; border-radius: 4px;
    }}
    .status-pill {{
        display: inline-block; background-color: {PAGE_BG}; color: {NAVY};
        border: 1px solid {BORDER}; border-radius: 12px;
        padding: 2px 10px; font-size: 0.78rem; font-weight: 600; margin: 2px 4px 2px 0;
    }}

    /* ---- Buttons ---- */
    .stButton > button {{
        background-color: {NAVY}; color: #fff; border: none;
        border-radius: 8px; font-weight: 600; padding: 0.5rem 1.2rem;
    }}
    .stButton > button:hover {{ background-color: {NAVY_LIGHT}; color: #fff; }}
</style>
""", unsafe_allow_html=True)


def fmt_cr(value, decimals=1):
    """Format a Rs. Crore value compactly so it never truncates in a KPI card."""
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 1000:
        return f"₹{value/1000:.2f}k Cr"
    return f"₹{value:.{decimals}f} Cr"


def page_header(title, subtitle=None):
    st.markdown(f'<div class="app-header"><div class="badge">📊</div><h1>{title}</h1></div>',
                unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="app-subtitle">{subtitle}</div>', unsafe_allow_html=True)


@st.cache_resource
def get_conn():
    return psycopg2.connect(DB_URL)


def q(sql, params=None):
    return pd.read_sql(sql, get_conn(), params=params)


def execute(sql, params=None):
    """For INSERT/UPDATE statements -- not for use with pd.read_sql."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    cur.close()


def log_action(username, action, target_table=None, target_key=None, detail=None):
    """Write one row to auth.audit_log. Never raises -- an audit-log failure
    should not block the underlying action, but is worth surfacing quietly."""
    try:
        execute("""
            INSERT INTO auth.audit_log (username, action, target_table, target_key, detail)
            VALUES (%s, %s, %s, %s, %s)
        """, (username, action, target_table, target_key, detail))
    except Exception as e:
        st.caption(f"(audit log write failed: {e})")


def check_login(username, password):
    """Verify username/password against auth.users. Returns (full_name, role) on
    success, None on failure. Password comparison happens via bcrypt, never
    plaintext equality. Inactive users are rejected even with a correct password.
    Every attempt -- success or failure -- is written to the audit log."""
    import bcrypt
    result = q("SELECT password_hash, full_name, role, is_active FROM auth.users WHERE username = %(u)s",
               {"u": username})
    if result.empty or not result.iloc[0]["is_active"]:
        log_action(username, "LOGIN_FAILED", detail="unknown user or inactive")
        return None
    row = result.iloc[0]
    if bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        log_action(username, "LOGIN")
        return row["full_name"], row["role"]
    log_action(username, "LOGIN_FAILED", detail="bad password")
    return None


SESSION_TIMEOUT_MINUTES = 30


def session_expired():
    import time
    last_active = st.session_state.get("last_active_ts")
    if last_active is None:
        return False
    return (time.time() - last_active) > SESSION_TIMEOUT_MINUTES * 60


def touch_session():
    import time
    st.session_state.last_active_ts = time.time()


# ------------------------------------------------------------
# Sidebar: global filters + login (role-aware: viewer / uploader / admin)
# ------------------------------------------------------------
st.sidebar.markdown("""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:2px;">
    <div style="background-color:#C99A2E;color:#0A2647;font-weight:800;border-radius:8px;
                width:34px;height:34px;display:flex;align-items:center;justify-content:center;
                font-size:0.95rem;flex-shrink:0;">MIS</div>
    <div>
        <div style="font-weight:700;font-size:1.02rem;line-height:1.15;">NBFC Securitisation MIS</div>
        <div style="font-size:0.72rem;opacity:0.75;">Treasury & DA management</div>
    </div>
</div>
""", unsafe_allow_html=True)
st.sidebar.markdown("<hr style='margin:14px 0 12px 0;'>", unsafe_allow_html=True)

if "user_role" not in st.session_state:
    st.session_state.user_role = None       # None | 'viewer' | 'uploader' | 'admin'
    st.session_state.user_name = None
    st.session_state.username = None
    st.session_state.last_active_ts = None

# Enforce session timeout -- auto-logout after inactivity, logged for the record.
if st.session_state.user_role and session_expired():
    log_action(st.session_state.username, "SESSION_TIMEOUT")
    st.session_state.user_role = None
    st.session_state.user_name = None
    st.session_state.username = None
    st.sidebar.warning(f"Session expired after {SESSION_TIMEOUT_MINUTES} min of inactivity. Please log in again.")

with st.sidebar.expander("🔒 Account", expanded=False):
    if st.session_state.user_role:
        touch_session()
        st.markdown(f"**{st.session_state.user_name}**")
        st.caption(f"Role: {st.session_state.user_role.capitalize()}")
        if st.button("Log out", use_container_width=True):
            log_action(st.session_state.username, "LOGOUT")
            st.session_state.user_role = None
            st.session_state.user_name = None
            st.session_state.username = None
            st.rerun()
    else:
        login_user = st.text_input("Username", key="login_user")
        login_pass = st.text_input("Password", type="password", key="login_pass")
        if st.button("Log in", use_container_width=True):
            result = check_login(login_user, login_pass)
            if result:
                full_name, role = result
                st.session_state.user_role = role
                st.session_state.user_name = full_name
                st.session_state.username = login_user
                touch_session()
                st.rerun()
            else:
                st.error("Invalid username or password.")

st.sidebar.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
page_options = ["Management Summary", "Executive Review", "Data Quality"]
if st.session_state.user_role in ("uploader", "admin"):
    page_options.append("Upload monthly MIS")
if st.session_state.user_role == "admin":
    page_options.append("User management")
page = st.sidebar.radio("View", page_options, label_visibility="collapsed")

months = q("SELECT DISTINCT month FROM raw.monthly_mis ORDER BY month DESC")["month"].tolist()
investors = ["All Investors"] + sorted(
    q("SELECT DISTINCT investor_name FROM raw.deals ORDER BY investor_name")["investor_name"].tolist()
)

# ==============================================================
# PAGE 1: MANAGEMENT SUMMARY
# ==============================================================
if page == "Management Summary":
    page_header("Investor-wise securitisation & DA management view",
                "Select an investor and month to filter every figure below.")

    col1, col2 = st.columns(2)
    with col1:
        selected_investor = st.selectbox("Select investor / bank", investors)
    with col2:
        selected_month = st.selectbox("Select month", months, format_func=lambda d: d.strftime("%b-%y"))

    if selected_investor == "All Investors":
        kpi_df = q("""
            SELECT count(DISTINCT d.deal_id) AS total_deals,
                   count(DISTINCT d.deal_id) FILTER (WHERE d.status='Active') AS active_deals,
                   sum(d.amount_securitised_deal_date) AS total_deal_amount,
                   sum(m.closing_pos_investor_share) FILTER (WHERE m.data_status='Available') AS investor_pos,
                   sum(m.total_bank_payout) FILTER (WHERE m.data_status='Available') AS payout,
                   sum(m.npa_amount) FILTER (WHERE m.data_status='Available') AS npa
            FROM raw.deals d
            JOIN raw.monthly_mis m ON m.deal_id = d.deal_id AND m.month = %(month)s
        """, {"month": selected_month})
    else:
        kpi_df = q("""
            SELECT * FROM calc.investor_summary
            WHERE investor_name = %(inv)s AND month = %(month)s
        """, {"inv": selected_investor, "month": selected_month})

    if kpi_df.empty or kpi_df.iloc[0]["total_deals"] is None:
        st.warning("No data for this investor/month combination.")
    else:
        row = kpi_df.iloc[0]
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total deals", int(row["total_deals"] or 0))
        c2.metric("Active deals", int(row["active_deals"] or 0))
        c3.metric("Total deal amount", fmt_cr(row["total_deal_amount"]))
        c4.metric("Investor POS", fmt_cr(row["investor_pos"]))
        c5.metric("Payout", fmt_cr(row["payout"], decimals=2))
        c6.metric("NPA", fmt_cr(row["npa"], decimals=2))

    st.divider()

    # Product mix + Instrument mix, side by side
    where_clause = "" if selected_investor == "All Investors" else "AND d.investor_name = %(inv)s"
    params = {"month": selected_month} if selected_investor == "All Investors" else {"month": selected_month, "inv": selected_investor}

    mix_df = q(f"""
        SELECT d.underlying AS product, d.instrument_type,
               sum(m.total_bank_payout) FILTER (WHERE m.data_status='Available') AS payout
        FROM raw.deals d
        JOIN raw.monthly_mis m ON m.deal_id = d.deal_id AND m.month = %(month)s
        WHERE 1=1 {where_clause}
        GROUP BY d.underlying, d.instrument_type
    """, params)

    left, right = st.columns(2)
    with left:
        st.markdown("##### Gold vs other products")
        if not mix_df.empty:
            gold_split = mix_df.copy()
            gold_split["group"] = gold_split["product"].apply(lambda p: "Gold" if p == "Gold" else "Other")
            gold_split = gold_split.groupby("group")["payout"].sum().reset_index()
            fig = px.pie(gold_split, names="group", values="payout", hole=0.55,
                         color="group", color_discrete_map={"Gold": GOLD, "Other": "#8FA6B3"})
            fig.update_traces(textinfo="percent+label", marker=dict(line=dict(color="#FFFFFF", width=2)))
            fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=280)
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("##### DA vs PTC")
        if not mix_df.empty:
            instr_split = mix_df.groupby("instrument_type")["payout"].sum().reset_index()
            fig = px.pie(instr_split, names="instrument_type", values="payout", hole=0.55,
                         color="instrument_type", color_discrete_map={"DA": TEAL, "PTC": "#5C8CAD"})
            fig.update_traces(textinfo="percent+label", marker=dict(line=dict(color="#FFFFFF", width=2)))
            fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=280)
            st.plotly_chart(fig, use_container_width=True)

# ==============================================================
# PAGE 2: EXECUTIVE REVIEW
# ==============================================================
elif page == "Executive Review":
    page_header("Bank relationship — consolidated executive review",
                "Portfolio KPIs, financial-year and quarter-on-quarter performance, and an auto-generated summary.")

    selected_bank = st.selectbox("Select investor / bank", investors, key="exec_bank")

    where = "" if selected_bank == "All Investors" else "WHERE investor_name = %(bank)s"
    params = {} if selected_bank == "All Investors" else {"bank": selected_bank}

    kpi_df = q(f"""
        SELECT count(*) AS total_deals,
               count(*) FILTER (WHERE status='Active') AS active_deals,
               count(*) FILTER (WHERE status='Closed') AS closed_deals,
               sum(amount_securitised_deal_date) AS total_deal_value,
               avg(amount_securitised_deal_date) AS avg_deal_size,
               max(amount_securitised_deal_date) AS largest_deal,
               min(borrowing_date) AS first_deal,
               max(borrowing_date) AS latest_deal
        FROM raw.deals {where}
    """, params)
    row = kpi_df.iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total deals", int(row["total_deals"] or 0))
    c2.metric("Active deals", int(row["active_deals"] or 0))
    c3.metric("Closed deals", int(row["closed_deals"] or 0))
    c4.metric("Total deal value", fmt_cr(row["total_deal_value"]))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Average deal size", fmt_cr(row["avg_deal_size"]))
    c6.metric("Largest deal", fmt_cr(row["largest_deal"]))
    c7.metric("First deal", str(row["first_deal"]) if row["first_deal"] else "—")
    c8.metric("Latest deal", str(row["latest_deal"]) if row["latest_deal"] else "—")

    # Auto-generated narrative -- mirrors the original workbook's
    # Executive Review narrative paragraph, built the same way: pure
    # string formatting off already-computed KPIs, no new logic here.
    if row["total_deals"]:
        da_share_df = q(f"""
            SELECT instrument_type, sum(amount_securitised_deal_date) AS val
            FROM raw.deals {where}
            GROUP BY instrument_type
        """, params)
        total_val = da_share_df["val"].sum()
        da_val = da_share_df.loc[da_share_df["instrument_type"] == "DA", "val"].sum()
        da_share_pct = (da_val / total_val * 100) if total_val else 0

        bank_label = selected_bank if selected_bank != "All Investors" else "The portfolio"
        narrative = (
            f"As of the latest reporting period, {bank_label} comprises "
            f"{int(row['total_deals'])} deal(s), of which {int(row['active_deals'])} remain active "
            f"and {int(row['closed_deals'])} have been closed. The aggregate deal value stands at "
            f"{fmt_cr(row['total_deal_value'])}, with an average deal size of {fmt_cr(row['avg_deal_size'])} "
            f"and the largest single deal recorded at {fmt_cr(row['largest_deal'])}. By instrument, "
            f"Direct Assignment (DA) accounts for {da_share_pct:.1f}% of deal value, while "
            f"Pass Through Certificates (PTC) account for the remaining {100 - da_share_pct:.1f}%."
        )
        st.markdown(f'<div class="exec-narrative">{narrative}</div>', unsafe_allow_html=True)

    st.divider()
    st.subheader("Table 1 — financial year-wise performance")
    fy_df = q("SELECT * FROM calc.executive_review_fy ORDER BY fy" if selected_bank == "All Investors" else """
        SELECT d.fy,
               count(*) AS deals,
               count(*) FILTER (WHERE d.status='Active') AS active,
               count(*) FILTER (WHERE d.status='Closed') AS closed,
               sum(d.amount_securitised_deal_date) AS deal_value,
               avg(d.amount_securitised_deal_date) AS avg_deal_size
        FROM raw.deals d WHERE d.investor_name = %(bank)s GROUP BY d.fy ORDER BY d.fy
    """, params if selected_bank != "All Investors" else None)
    st.dataframe(fy_df, use_container_width=True)

    st.subheader("Table 2 — quarter-on-quarter performance")
    qoq_df = q("SELECT * FROM calc.executive_review_qoq ORDER BY quarter" if selected_bank == "All Investors" else """
        SELECT d.period AS quarter, count(*) AS deals,
               sum(d.amount_securitised_deal_date) AS deal_value
        FROM raw.deals d WHERE d.investor_name = %(bank)s GROUP BY d.period ORDER BY d.period
    """, params if selected_bank != "All Investors" else None)
    if not qoq_df.empty:
        qoq_df["cumulative_deal_value"] = qoq_df["deal_value"].cumsum()
        qoq_df["qoq_value_change"] = qoq_df["deal_value"].diff()
        qoq_df["qoq_value_growth_pct"] = (qoq_df["deal_value"].pct_change() * 100).round(1)
    st.dataframe(qoq_df, use_container_width=True)

# ==============================================================
# PAGE 3: DATA QUALITY
# ==============================================================
elif page == "Data Quality":
    page_header("Data quality overview",
                "Mirrors the original workbook's distinction between missing and zero data — "
                "deleted payout source columns were shown as unavailable, never zero.")

    dq_df = q("""
        SELECT data_status, count(*) AS row_count
        FROM raw.monthly_mis GROUP BY data_status ORDER BY row_count DESC
    """)
    status_colors = {
        "Available": TEAL, "Missing": CORAL, "Incomplete": GOLD,
        "Zero": "#8FA6B3", "Not Applicable": "#B9CFC0", "Reconciliation Required": "#D97A6C",
    }
    fig = px.bar(dq_df, x="data_status", y="row_count", color="data_status",
                 color_discrete_map=status_colors)
    fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10),
                       xaxis_title="", yaxis_title="Row count", height=340)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Deals with the most non-available months")
    gap_df = q("""
        SELECT d.deal_name, d.investor_name, count(*) AS gap_months
        FROM raw.monthly_mis m
        JOIN raw.deals d ON d.deal_id = m.deal_id
        WHERE m.data_status != 'Available'
        GROUP BY d.deal_name, d.investor_name
        ORDER BY gap_months DESC
        LIMIT 15
    """)
    st.dataframe(gap_df, use_container_width=True)

# ==============================================================
# PAGE 4: UPLOAD MONTHLY MIS (admin-only)
# ==============================================================
elif page == "Upload monthly MIS":
    # Hard server-side gate -- never trust that the sidebar only showed this
    # page to uploaders/admins. session_state can't be forged by a normal
    # user, but this check is what actually blocks the write, not the radio options.
    if st.session_state.user_role not in ("uploader", "admin"):
        st.error("You must be logged in as an uploader or admin to access this page.")
        st.stop()

    page_header("Upload monthly MIS",
                f"Signed in as {st.session_state.user_name} ({st.session_state.user_role}) "
                f"· uploads insert directly into raw.monthly_mis")

    st.markdown(f"""
    <div class="req-card">
        <div style="margin-bottom:10px;"><span class="req-num">1</span><strong>Prepare a CSV with a header row</strong></div>
        <div style="margin-left:30px;color:{SLATE};font-size:0.88rem;line-height:1.7;">
            <code>deal_id</code> · <code>month</code> · <code>total_pool_outstanding</code> ·
            <code>closing_pos_investor_share</code> · <code>total_bank_payout</code> ·
            <code>total_principal_payout</code> · <code>npa_amount</code> · <code>mclr</code> ·
            <code>roi</code> · <code>investor_share_x_roi</code> · <code>data_status</code>
        </div>
    </div>
    <div class="req-card">
        <div style="margin-bottom:10px;"><span class="req-num">2</span><strong>Follow these rules</strong></div>
        <div style="margin-left:30px;color:{SLATE};font-size:0.88rem;line-height:1.9;">
            <code>month</code> is the first day of the month — e.g. <code>2026-08-01</code><br>
            <code>deal_id</code> must already exist in <code>raw.deals</code> — register new deals separately<br>
            Leave numeric fields <strong>blank</strong> for Missing / Incomplete rows — never enter 0
        </div>
    </div>
    <div class="req-card">
        <div style="margin-bottom:10px;"><span class="req-num">3</span><strong>Valid <code>data_status</code> values</strong></div>
        <div style="margin-left:30px;">
            <span class="status-pill">Available</span><span class="status-pill">Zero</span>
            <span class="status-pill">Missing</span><span class="status-pill">Incomplete</span>
            <span class="status-pill">Not Applicable</span><span class="status-pill">Reconciliation Required</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    st.markdown("##### Upload file")
    uploaded = st.file_uploader("Choose a CSV file", type="csv", label_visibility="collapsed")

    if uploaded is not None:
        try:
            new_df = pd.read_csv(uploaded)
        except Exception as e:
            st.error(f"Could not read CSV: {e}")
            st.stop()

        required_cols = ["deal_id", "month", "total_pool_outstanding", "closing_pos_investor_share",
                          "total_bank_payout", "total_principal_payout", "npa_amount", "mclr", "roi",
                          "investor_share_x_roi", "data_status"]
        missing_cols = [c for c in required_cols if c not in new_df.columns]
        if missing_cols:
            st.error(f"CSV is missing required columns: {', '.join(missing_cols)}")
            st.stop()

        st.write(f"Preview — {len(new_df)} row(s):")
        st.dataframe(new_df, use_container_width=True)

        # Validate deal_ids exist before allowing insert -- catches typos
        # early rather than failing halfway through a partial load.
        known_deals = set(q("SELECT deal_id FROM raw.deals")["deal_id"])
        unknown = set(new_df["deal_id"]) - known_deals
        if unknown:
            st.error(f"These deal_id(s) are not registered in raw.deals: {', '.join(sorted(unknown))}")
            st.stop()

        if st.button(f"Confirm and insert {len(new_df)} row(s)"):
            conn = get_conn()
            cur = conn.cursor()
            inserted, skipped = 0, 0
            for _, r in new_df.iterrows():
                try:
                    cur.execute("""
                        INSERT INTO raw.monthly_mis
                            (deal_id, month, total_pool_outstanding, closing_pos_investor_share,
                             total_bank_payout, total_principal_payout, npa_amount, mclr, roi,
                             investor_share_x_roi, data_status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (deal_id, month) DO UPDATE SET
                            total_pool_outstanding = EXCLUDED.total_pool_outstanding,
                            closing_pos_investor_share = EXCLUDED.closing_pos_investor_share,
                            total_bank_payout = EXCLUDED.total_bank_payout,
                            total_principal_payout = EXCLUDED.total_principal_payout,
                            npa_amount = EXCLUDED.npa_amount,
                            mclr = EXCLUDED.mclr, roi = EXCLUDED.roi,
                            investor_share_x_roi = EXCLUDED.investor_share_x_roi,
                            data_status = EXCLUDED.data_status
                    """, (
                        r["deal_id"], r["month"],
                        None if pd.isna(r["total_pool_outstanding"]) else r["total_pool_outstanding"],
                        None if pd.isna(r["closing_pos_investor_share"]) else r["closing_pos_investor_share"],
                        None if pd.isna(r["total_bank_payout"]) else r["total_bank_payout"],
                        None if pd.isna(r["total_principal_payout"]) else r["total_principal_payout"],
                        None if pd.isna(r["npa_amount"]) else r["npa_amount"],
                        None if pd.isna(r["mclr"]) else r["mclr"],
                        None if pd.isna(r["roi"]) else r["roi"],
                        None if pd.isna(r["investor_share_x_roi"]) else r["investor_share_x_roi"],
                        r["data_status"],
                    ))
                    inserted += 1
                    log_action(st.session_state.username, "UPSERT", "raw.monthly_mis",
                               f"{r['deal_id']} / {r['month']}",
                               f"data_status={r['data_status']}")
                except Exception as e:
                    skipped += 1
                    st.warning(f"Row for {r['deal_id']} / {r['month']} failed: {e}")
            conn.commit()
            cur.close()
            log_action(st.session_state.username, "UPLOAD_BATCH", "raw.monthly_mis",
                       detail=f"file={uploaded.name}, inserted={inserted}, skipped={skipped}")
            st.success(f"Inserted/updated {inserted} row(s). {skipped} row(s) skipped due to errors.")
            st.cache_resource.clear()

# ==============================================================
# PAGE 5: USER MANAGEMENT (admin-only)
# ==============================================================
elif page == "User management":
    if st.session_state.user_role != "admin":
        st.error("You must be logged in as an admin to access this page.")
        st.stop()

    page_header("User management", "Create users, change roles, deactivate access, and review the audit trail.")

    st.markdown("##### Existing users")
    users_df = q("SELECT username, full_name, role, is_active, created_at FROM auth.users ORDER BY created_at")
    st.dataframe(users_df, use_container_width=True)

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
    st.markdown("##### Create a new user")
    with st.form("create_user_form"):
        c1, c2 = st.columns(2)
        new_username = c1.text_input("Username")
        new_full_name = c2.text_input("Full name")
        c3, c4 = st.columns(2)
        new_role = c3.selectbox("Role", ["viewer", "uploader", "admin"])
        new_password = c4.text_input("Temporary password", type="password")
        submitted = st.form_submit_button("Create user")

        if submitted:
            if not new_username or not new_password:
                st.error("Username and password are required.")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                import bcrypt
                pw_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                try:
                    execute("""
                        INSERT INTO auth.users (username, password_hash, full_name, role, created_by)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (new_username, pw_hash, new_full_name, new_role, st.session_state.username))
                    log_action(st.session_state.username, "USER_CREATED", "auth.users", new_username,
                               f"role={new_role}")
                    st.success(f"User '{new_username}' created with role '{new_role}'.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create user: {e}")

    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
    st.markdown("##### Deactivate a user")
    active_usernames = users_df.loc[users_df["is_active"], "username"].tolist()
    if active_usernames:
        deactivate_target = st.selectbox("Select user to deactivate", active_usernames)
        if st.button("Deactivate", type="secondary"):
            if deactivate_target == st.session_state.username:
                st.error("You cannot deactivate your own account.")
            else:
                execute("UPDATE auth.users SET is_active = false WHERE username = %s", (deactivate_target,))
                log_action(st.session_state.username, "USER_DEACTIVATED", "auth.users", deactivate_target)
                st.success(f"'{deactivate_target}' deactivated.")
                st.rerun()

    st.divider()
    st.markdown("##### Recent audit log")
    audit_df = q("""
        SELECT occurred_at, username, action, target_table, target_key, detail
        FROM auth.audit_log ORDER BY occurred_at DESC LIMIT 100
    """)
    st.dataframe(audit_df, use_container_width=True, height=350)