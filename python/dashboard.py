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

import risk_metrics
import stress_engine

st.set_page_config(page_title="NBFC Securitisation MIS", layout="wide", page_icon="📊")

DB_URL = os.environ.get("NBFC_MIS_DB_URL")
if not DB_URL:
    st.error("Set NBFC_MIS_DB_URL as an environment variable before running.")
    st.stop()

# ------------------------------------------------------------
# Design tokens -- institutional-finance palette: light surfaces,
# navy as the single brand/ink color, gold reserved for two small
# accent badges only (not large blocks). Categorical chart colors
# (CHART_SEQ) are the dataviz skill's validated 7-slot order, run
# through scripts/validate_palette.js against this app's own white
# card surface (all hard gates pass; three of the seven -- aqua,
# yellow, magenta -- sit under 3:1 contrast on white, so every chart
# using them keeps visible on-chart labels rather than relying on a
# hover-only legend, per the skill's "relief rule").
# ------------------------------------------------------------
INK = "#101828"           # primary text / headings
NAVY = "#173A66"          # brand / active states / primary buttons
NAVY_SOFT = "#EAF1FC"     # light navy tint -- active nav item, callout backgrounds
SLATE = "#5B6472"         # secondary / muted text
GOLD = "#B8862E"          # accent -- reserved for two small badge chips only
GOLD_SOFT = "#FBF3E1"     # gold tint, used sparingly
TEAL = "#0F8F82"          # secondary data accent (DA, positive)
CORAL = "#D6493B"         # risk / NPA / missing-data accent
SURFACE = "#FFFFFF"       # card background
PAGE_BG = "#F7F8FA"       # app background -- light, airy neutral
BORDER = "#E5E8EC"        # hairline borders
CHART_SEQ = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#4A3AA7", "#E34948"]

px.defaults.color_discrete_sequence = CHART_SEQ
px.defaults.template = "plotly_white"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap');

    html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}
    .stApp {{ background-color: {PAGE_BG}; }}
    .block-container {{ padding-top: 1.75rem; max-width: 1280px; }}

    /* ---- Sidebar -- light, not a solid dark block ---- */
    section[data-testid="stSidebar"] {{
        background-color: {SURFACE};
        border-right: 1px solid {BORDER};
    }}
    section[data-testid="stSidebar"] * {{ color: {INK} !important; }}
    section[data-testid="stSidebar"] hr {{ border-color: {BORDER}; }}
    .sidebar-tagline {{ color: {SLATE} !important; }}

    /* Radio nav restyled as a vertical list, active item picked out with
       a soft navy tint + left rule rather than a solid color block */
    section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 2px; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        background-color: transparent;
        border-radius: 8px;
        padding: 9px 12px !important;
        margin: 0 !important;
        border-left: 3px solid transparent;
        transition: background-color 0.15s ease, border-color 0.15s ease;
        width: 100%;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
        background-color: {PAGE_BG};
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label[data-checked="true"],
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {{
        background-color: {NAVY_SOFT} !important;
        border-left: 3px solid {NAVY};
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {{
        color: {NAVY} !important;
        font-weight: 600 !important;
    }}
    section[data-testid="stSidebar"] .stExpander {{
        border: 1px solid {BORDER};
        border-radius: 8px;
        background-color: {PAGE_BG};
    }}

    /* ---- Header bar ---- */
    .app-header {{
        display: flex; align-items: center; gap: 12px;
        padding-bottom: 4px; margin-bottom: 4px;
    }}
    .app-header .badge {{
        background-color: {GOLD}; color: #fff; font-weight: 700;
        border-radius: 8px; width: 40px; height: 40px;
        display: flex; align-items: center; justify-content: center;
        font-size: 1.1rem; flex-shrink: 0;
    }}
    .app-header h1 {{ margin: 0 !important; font-size: 1.6rem !important; }}
    .app-subtitle {{ color: {SLATE}; font-size: 0.92rem; margin-top: -2px; margin-bottom: 1.2rem; }}

    /* ---- KPI cards -- plain and light, no loud color bars ---- */
    div[data-testid="stMetric"] {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 16px 18px 14px 18px;
        box-shadow: 0 1px 2px rgba(16,24,40,0.04);
    }}
    div[data-testid="stMetricLabel"] {{
        color: {SLATE} !important; font-weight: 600 !important;
        font-size: 0.76rem !important; text-transform: uppercase; letter-spacing: 0.03em;
    }}
    /* Streamlit's own base styling can ellipsis-truncate a metric value in
       a narrow column -- override every layer explicitly (the value can
       be wrapped in an extra inner div depending on Streamlit version) so
       a number is NEVER clipped with "...", only ever wrapped to a second
       line in the rare case it's genuinely too wide. */
    div[data-testid="stMetricValue"],
    div[data-testid="stMetricValue"] * {{
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        word-break: break-word !important;
    }}
    div[data-testid="stMetricValue"] {{
        color: {NAVY} !important;
        font-size: 1.4rem !important; font-weight: 700 !important;
        line-height: 1.3 !important;
    }}

    /* ---- Typography ---- */
    h1 {{ color: {INK}; font-weight: 700; letter-spacing: -0.01em; }}
    h2, h3 {{ color: {INK}; font-weight: 600; }}
    h4, h5, .stMarkdown strong {{ color: {INK}; }}
    p, .stMarkdown, .stCaption {{ color: {INK}; }}

    /* ---- Narrative / callout box ---- */
    .exec-narrative {{
        background-color: {NAVY_SOFT};
        border-left: 4px solid {NAVY};
        border-radius: 8px;
        padding: 18px 22px;
        color: {INK};
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
        background-color: {PAGE_BG}; color: {NAVY};
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
    .stButton > button:hover {{ background-color: #0F2C52; color: #fff; }}
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


def _reconnect():
    """Neon's free-tier compute auto-suspends after a few minutes idle,
    which silently kills whatever connection get_conn() had cached --
    the next query then raises OperationalError/InterfaceError ("SSL
    connection has been closed unexpectedly"), not because anything is
    misconfigured. Clearing the cached resource forces a fresh connect
    on the next get_conn() call."""
    get_conn.clear()
    return get_conn()


def q(sql, params=None):
    try:
        return pd.read_sql(sql, get_conn(), params=params)
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        return pd.read_sql(sql, _reconnect(), params=params)


def execute(sql, params=None):
    """For INSERT/UPDATE statements -- not for use with pd.read_sql."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        cur.close()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        conn = _reconnect()
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
st.sidebar.markdown(f"""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:2px;">
    <div style="background-color:{GOLD};color:#fff;font-weight:800;border-radius:8px;
                width:34px;height:34px;display:flex;align-items:center;justify-content:center;
                font-size:0.95rem;flex-shrink:0;">MIS</div>
    <div>
        <div style="font-weight:700;font-size:1.02rem;line-height:1.15;color:{INK};">NBFC Securitisation MIS</div>
        <div class="sidebar-tagline" style="font-size:0.72rem;">Treasury & DA management</div>
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
page_options = ["Management Summary", "Executive Review", "Data Quality",
                 "Waterfall & Credit Enhancement", "Credit Performance", "Stress Testing",
                 "Funding & ALM", "Regulatory Compliance"]
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
        c1, c2, c3 = st.columns(3)
        c1.metric("Total deals", int(row["total_deals"] or 0))
        c2.metric("Active deals", int(row["active_deals"] or 0))
        c3.metric("Total deal amount", fmt_cr(row["total_deal_amount"]))
        c4, c5, c6 = st.columns(3)
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
# PAGE 4: WATERFALL & CREDIT ENHANCEMENT
# Reads raw.monthly_waterfall + raw.deals / calc.ce_cover_ratio_trend
# (sql/05_schema_waterfall.sql). Only PTC deals with a tranche appear
# here -- DA deals have no waterfall row by design (see that file's
# assumption #9). Same "zero business logic in this file" rule as the
# rest of the dashboard: every number is a straight read from calc.*.
# ==============================================================
elif page == "Waterfall & Credit Enhancement":
    page_header("Waterfall & credit enhancement",
                "PTC deals only — DA deals aren't tranched and carry no CE waterfall. "
                "Pick a deal to see its monthly payment priority and CE cover ratio trend.")

    wf_deals = q("""
        SELECT DISTINCT d.deal_id, d.deal_name, d.tranche, d.credit_enhancement_type,
               d.credit_enhancement_initial_amount, d.investor_name
        FROM raw.deals d
        JOIN raw.monthly_waterfall w ON w.deal_id = d.deal_id
        ORDER BY d.deal_name
    """)

    if wf_deals.empty:
        st.info("No monthly_waterfall data found yet — run generate_synthetic_data.py "
                "and load_to_postgres.py after applying sql/05_schema_waterfall.sql.")
    else:
        deal_label = {
            row.deal_id: f"{row.deal_name} · {row.tranche} · {row.credit_enhancement_type}"
            for row in wf_deals.itertuples()
        }
        selected_deal_id = st.selectbox(
            "Select deal", wf_deals["deal_id"], format_func=lambda x: deal_label[x]
        )
        deal_row = wf_deals.loc[wf_deals["deal_id"] == selected_deal_id].iloc[0]

        c1, c2, c3 = st.columns(3)
        c1.metric("Tranche", deal_row["tranche"])
        c2.metric("CE type", deal_row["credit_enhancement_type"])
        c3.metric("CE initial amount", fmt_cr(deal_row["credit_enhancement_initial_amount"], decimals=2))

        st.divider()

        wf_hist = q("""
            SELECT month, collections_available, servicing_fee_paid, senior_interest_paid,
                   senior_principal_paid, subordinate_interest_paid, subordinate_principal_paid,
                   excess_spread_to_originator, ce_opening_balance, ce_drawn_this_month,
                   ce_closing_balance, ce_cover_ratio
            FROM raw.monthly_waterfall
            WHERE deal_id = %(d)s ORDER BY month
        """, {"d": selected_deal_id})

        st.subheader("Monthly waterfall")
        selected_wf_month = st.selectbox(
            "Select month", wf_hist["month"], format_func=lambda d: d.strftime("%b-%y"),
            index=len(wf_hist) - 1,
        )
        month_row = wf_hist.loc[wf_hist["month"] == selected_wf_month].iloc[0]

        # Priority order, top to bottom -- see assumption #5 in
        # sql/05_schema_waterfall.sql (standard convention, not confirmed
        # against a specific deal's payment waterfall clause).
        waterfall_lines = pd.DataFrame([
            {"line": "Servicing fee", "amount": month_row["servicing_fee_paid"]},
            {"line": "Senior interest", "amount": month_row["senior_interest_paid"]},
            {"line": "Senior principal", "amount": month_row["senior_principal_paid"]},
            {"line": "Subordinate interest", "amount": month_row["subordinate_interest_paid"]},
            {"line": "Subordinate principal", "amount": month_row["subordinate_principal_paid"]},
            {"line": "Excess spread to originator", "amount": month_row["excess_spread_to_originator"]},
        ])
        fig = px.bar(waterfall_lines, x="amount", y="line", orientation="h",
                     color="line", color_discrete_sequence=CHART_SEQ)
        fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=320,
                           xaxis_title="Rs. Crs", yaxis_title="", yaxis=dict(categoryorder="array",
                           categoryarray=waterfall_lines["line"].tolist()[::-1]))
        st.plotly_chart(fig, use_container_width=True)

        cA, cB, cC = st.columns(3)
        cA.metric("Collections available", fmt_cr(month_row["collections_available"], decimals=2))
        cB.metric("CE drawn this month", fmt_cr(month_row["ce_drawn_this_month"], decimals=3))
        cC.metric("CE closing balance", fmt_cr(month_row["ce_closing_balance"], decimals=2))
        if month_row["ce_drawn_this_month"] and month_row["ce_drawn_this_month"] > 0:
            st.warning(f"CE was drawn this month — a rough-collections month "
                       f"(proxied by elevated NPA; see assumption #6 in sql/05_schema_waterfall.sql).")

        st.divider()
        st.subheader("CE cover ratio trend")
        if deal_row["tranche"] == "Subordinate":
            st.caption("This is a Subordinate-tranche deal — there's no linked senior tranche's "
                       "POS in this schema to compute a cover ratio against (see assumption #2/#8 "
                       "in sql/05_schema_waterfall.sql), so it isn't shown here.")
        else:
            threshold = st.slider("Flag threshold (CE cover ratio, x)", 0.0, 3.0, 1.0, 0.1)
            cover_df = wf_hist.dropna(subset=["ce_cover_ratio"])
            fig2 = px.line(cover_df, x="month", y="ce_cover_ratio", markers=True)
            fig2.update_traces(line_color=NAVY, marker=dict(color=NAVY))
            fig2.add_hline(y=threshold, line_dash="dash", line_color=CORAL,
                            annotation_text=f"Threshold {threshold:.1f}x", annotation_position="top left")
            fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=340,
                                xaxis_title="", yaxis_title="CE cover ratio (x)")
            st.plotly_chart(fig2, use_container_width=True)

            breaches = cover_df.loc[cover_df["ce_cover_ratio"] < threshold]
            if not breaches.empty:
                st.error(f"CE cover ratio fell below {threshold:.1f}x in "
                         f"{len(breaches)} month(s), most recently "
                         f"{breaches['month'].max().strftime('%b-%y')}.")
            else:
                st.success(f"CE cover ratio has stayed at or above {threshold:.1f}x throughout.")

# ==============================================================
# PAGE 5: CREDIT PERFORMANCE
# Reads raw.monthly_delinquency, calc.roll_rate_*, calc.vintage_default_curve,
# and calc.deal_smm_cpr (sql/06_schema_delinquency.sql). CPR/WAL math lives
# in risk_metrics.py, not here or in SQL -- see that module's docstring
# for the WAL closed-form assumption and the SMM/CPR convention used.
# ==============================================================
elif page == "Credit Performance":
    page_header("Credit performance — DPD, roll rates, vintages, CPR/WAL",
                "All instrument types (DPD isn't a PTC-only concept). "
                "See risk_metrics.py and sql/06_schema_delinquency.sql for the methodology and caveats.")

    dq_deals = q("""
        SELECT DISTINCT d.deal_id, d.deal_name
        FROM raw.deals d JOIN raw.monthly_delinquency dq ON dq.deal_id = d.deal_id
        ORDER BY d.deal_name
    """)

    if dq_deals.empty:
        st.info("No monthly_delinquency data found yet — run generate_synthetic_data.py "
                "and load_to_postgres.py after applying sql/06_schema_delinquency.sql.")
    else:
        st.subheader("DPD bucket trend")
        dpd_options = ["Pool-wide (all deals)"] + dq_deals["deal_id"].tolist()
        dpd_labels = {"Pool-wide (all deals)": "Pool-wide (all deals)",
                      **dict(zip(dq_deals["deal_id"], dq_deals["deal_name"]))}
        dpd_choice = st.selectbox("Select deal", dpd_options, format_func=lambda x: dpd_labels[x],
                                    key="dpd_deal")

        if dpd_choice == "Pool-wide (all deals)":
            dpd_df = q("""
                SELECT month, SUM(pos_0dpd) AS pos_0dpd, SUM(pos_1_30dpd) AS pos_1_30dpd,
                       SUM(pos_31_60dpd) AS pos_31_60dpd, SUM(pos_61_90dpd) AS pos_61_90dpd,
                       SUM(pos_90plus_dpd) AS pos_90plus_dpd
                FROM raw.monthly_delinquency GROUP BY month ORDER BY month
            """)
        else:
            dpd_df = q("""
                SELECT month, pos_0dpd, pos_1_30dpd, pos_31_60dpd, pos_61_90dpd, pos_90plus_dpd
                FROM raw.monthly_delinquency WHERE deal_id = %(d)s ORDER BY month
            """, {"d": dpd_choice})

        dpd_long = dpd_df.melt(id_vars="month",
                                value_vars=["pos_0dpd", "pos_1_30dpd", "pos_31_60dpd", "pos_61_90dpd", "pos_90plus_dpd"],
                                var_name="bucket", value_name="pos")
        bucket_labels = {"pos_0dpd": "Current", "pos_1_30dpd": "1-30 DPD", "pos_31_60dpd": "31-60 DPD",
                          "pos_61_90dpd": "61-90 DPD", "pos_90plus_dpd": "90+ DPD"}
        dpd_long["bucket"] = dpd_long["bucket"].map(bucket_labels)
        fig = px.area(dpd_long, x="month", y="pos", color="bucket",
                       category_orders={"bucket": list(bucket_labels.values())},
                       color_discrete_map={"Current": TEAL, "1-30 DPD": GOLD, "31-60 DPD": "#D9822B",
                                            "61-90 DPD": CORAL, "90+ DPD": "#7A2418"})
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=360,
                           xaxis_title="", yaxis_title="Rs. Crs")
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.subheader("Roll-rate matrix")
        st.caption("Rate = this month's higher-bucket balance ÷ last month's adjacent lower-bucket balance "
                   "— the standard approximation from snapshot bucket balances (see assumption #4, "
                   "sql/06_schema_delinquency.sql). Not a loan-level roll trace.")
        roll_scope = st.radio("Scope", ["Pool-wide", "By product"], horizontal=True, key="roll_scope")
        if roll_scope == "Pool-wide":
            roll_df = q("SELECT * FROM calc.roll_rate_pool_wide ORDER BY month")
        else:
            products_df = q("SELECT DISTINCT product, instrument_type FROM calc.roll_rate_by_product "
                             "ORDER BY product, instrument_type")
            products_df["label"] = products_df["product"] + " · " + products_df["instrument_type"]
            product_choice = st.selectbox("Product / instrument", products_df["label"])
            prod, instr = products_df.loc[products_df["label"] == product_choice, ["product", "instrument_type"]].iloc[0]
            roll_df = q("SELECT month, roll_0_to_30, roll_30_to_60, roll_60_to_90, roll_90_to_90plus "
                        "FROM calc.roll_rate_by_product WHERE product = %(p)s AND instrument_type = %(i)s "
                        "ORDER BY month", {"p": prod, "i": instr})

        if roll_df.empty or roll_df.drop(columns=["month"]).isna().all().all():
            st.info("Not enough consecutive months of data yet to compute a roll rate for this scope.")
        else:
            roll_matrix = roll_df.set_index("month")[
                ["roll_0_to_30", "roll_30_to_60", "roll_60_to_90", "roll_90_to_90plus"]
            ].T
            roll_matrix.index = ["0 → 1-30", "1-30 → 31-60", "31-60 → 61-90", "61-90 → 90+"]
            fig2 = px.imshow(roll_matrix, aspect="auto", color_continuous_scale="Oranges",
                              labels=dict(x="Month", y="Transition", color="Roll rate"))
            fig2.update_xaxes(tickformat="%b-%y")
            fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320)
            st.plotly_chart(fig2, use_container_width=True)

        st.divider()
        st.subheader("Cumulative default rate by vintage")
        vintage_df = q("SELECT * FROM calc.vintage_default_curve WHERE month_on_book >= 0 ORDER BY vintage_quarter, month_on_book")
        if vintage_df.empty:
            st.info("No vintage data available.")
        else:
            fig3 = px.line(vintage_df, x="month_on_book", y="cumulative_default_rate", color="vintage_quarter",
                            markers=True)
            fig3.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=380,
                                xaxis_title="Month on book", yaxis_title="Cumulative default rate",
                                yaxis_tickformat=".2%", legend_title="Vintage (origination quarter)")
            st.plotly_chart(fig3, use_container_width=True)

        st.divider()
        st.subheader("CPR & WAL trend")
        st.caption("WAL uses a closed-form constant-paydown-rate approximation, not a full projected cash-flow "
                   "schedule — see risk_metrics.py for the formula and why. Both are recomputed every month "
                   "from a trailing 3-month average of scheduled amortization + prepayment (SMM).")
        cpr_deal_choice = st.selectbox("Select deal", dq_deals["deal_id"],
                                         format_func=lambda x: dpd_labels.get(x, x), key="cpr_deal")
        smm_df = q("""
            SELECT deal_id, month, beginning_pos, scheduled_principal_payout, smm
            FROM calc.deal_smm_cpr WHERE deal_id = %(d)s ORDER BY month
        """, {"d": cpr_deal_choice})

        if smm_df.empty or smm_df["smm"].isna().all():
            st.info("Not enough consecutive 'Available' months for this deal to compute CPR/WAL yet.")
        else:
            trend_df = risk_metrics.add_rolling_cpr_wal(smm_df)
            c1, c2 = st.columns(2)
            with c1:
                fig4 = px.line(trend_df, x="month", y="cpr_trailing", markers=True)
                fig4.update_traces(line_color=NAVY, marker=dict(color=NAVY))
                fig4.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320,
                                    xaxis_title="", yaxis_title="Trailing annualized CPR", yaxis_tickformat=".1%")
                st.markdown("###### Annualized CPR (trailing 3-month)")
                st.plotly_chart(fig4, use_container_width=True)
            with c2:
                fig5 = px.line(trend_df, x="month", y="wal_years", markers=True)
                fig5.update_traces(line_color=TEAL, marker=dict(color=TEAL))
                fig5.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320,
                                    xaxis_title="", yaxis_title="WAL (years)")
                st.markdown("###### Weighted average life (approx.)")
                st.plotly_chart(fig5, use_container_width=True)

# ==============================================================
# PAGE 6: STRESS TESTING
# Forward-looking projection engine lives in stress_engine.py (which
# leans on risk_metrics.py for the paydown/WAL math) -- this page's job
# is ONLY to pull the deal's current state out of Postgres, build the
# plain dict stress_engine.run_stress_scenario() expects, and display
# the result. Senior-tranche PTC deals only -- see stress_engine.py's
# module docstring (assumption #7) for why.
# ==============================================================
elif page == "Stress Testing":
    page_header("Stress testing — CE cover ratio & WAL under shocked assumptions",
                "Demo-grade straight-line projection off each deal's own trailing roll rates — "
                "see stress_engine.py for the full methodology and every simplification made.")

    stress_deals = q("""
        SELECT DISTINCT d.deal_id, d.deal_name
        FROM raw.deals d
        JOIN raw.monthly_waterfall w ON w.deal_id = d.deal_id
        JOIN raw.monthly_delinquency dq ON dq.deal_id = d.deal_id
        WHERE d.tranche = 'Senior'
        ORDER BY d.deal_name
    """)

    if stress_deals.empty:
        st.info("No Senior-tranche deal has both waterfall and delinquency data yet — "
                "run generate_synthetic_data.py / load_to_postgres.py first.")
    else:
        deal_label_map = dict(zip(stress_deals["deal_id"], stress_deals["deal_name"]))
        stress_deal_id = st.selectbox("Select deal (Senior tranche only)", stress_deals["deal_id"],
                                        format_func=lambda x: deal_label_map[x])

        mis_hist = q("""
            SELECT month, closing_pos_investor_share, total_pool_outstanding,
                   total_principal_payout, scheduled_principal_payout
            FROM raw.monthly_mis
            WHERE deal_id = %(d)s AND data_status = 'Available'
            ORDER BY month
        """, {"d": stress_deal_id})
        mis_hist["beginning_pos"] = mis_hist["closing_pos_investor_share"].shift(1)
        mis_hist["prepayment_amount"] = (mis_hist["total_principal_payout"]
                                          - mis_hist["scheduled_principal_payout"]).clip(lower=0)
        denom = mis_hist["beginning_pos"] - mis_hist["scheduled_principal_payout"]
        mis_hist["smm"] = mis_hist["prepayment_amount"] / denom
        mis_hist.loc[denom <= 0, "smm"] = None
        mis_valid = mis_hist.dropna(subset=["beginning_pos"]).copy()
        mis_valid["deal_id"] = stress_deal_id

        dq_hist = q("""
            SELECT month, pos_0dpd, pos_1_30dpd, pos_31_60dpd, pos_61_90dpd, pos_90plus_dpd,
                   cumulative_default_amount
            FROM raw.monthly_delinquency WHERE deal_id = %(d)s ORDER BY month
        """, {"d": stress_deal_id})

        wf_hist = q("""
            SELECT month, ce_closing_balance FROM raw.monthly_waterfall
            WHERE deal_id = %(d)s ORDER BY month
        """, {"d": stress_deal_id})

        if len(mis_valid) < 3 or len(dq_hist) < 4 or wf_hist.empty:
            st.info("Not enough trailing history for this deal yet to build a stable set of "
                    "current-state inputs (need at least a few consecutive available months).")
        else:
            trend = risk_metrics.add_rolling_cpr_wal(
                mis_valid[["deal_id", "month", "beginning_pos", "scheduled_principal_payout", "smm"]]
            )
            latest_trend = trend.iloc[-1]

            dq_hist = dq_hist.merge(
                mis_hist[["month", "total_pool_outstanding"]], on="month", how="left"
            )
            dq_hist["f0"] = dq_hist["pos_0dpd"] / dq_hist["total_pool_outstanding"]
            dq_hist["f1"] = dq_hist["pos_1_30dpd"] / dq_hist["total_pool_outstanding"]
            dq_hist["f2"] = dq_hist["pos_31_60dpd"] / dq_hist["total_pool_outstanding"]
            dq_hist["f3"] = dq_hist["pos_61_90dpd"] / dq_hist["total_pool_outstanding"]
            dq_hist["f4"] = dq_hist["pos_90plus_dpd"] / dq_hist["total_pool_outstanding"]
            latest_dq = dq_hist.iloc[-1]

            # Trailing (last 6 obs) empirical roll rates -- same balance-snapshot
            # method as calc.roll_rate_deal_level, computed here in pandas since
            # we need it per-deal on demand rather than pre-materialized.
            r01 = (dq_hist["pos_1_30dpd"] / dq_hist["pos_0dpd"].shift(1)).tail(6).mean()
            r12 = (dq_hist["pos_31_60dpd"] / dq_hist["pos_1_30dpd"].shift(1)).tail(6).mean()
            r23 = (dq_hist["pos_61_90dpd"] / dq_hist["pos_31_60dpd"].shift(1)).tail(6).mean()
            r34 = (dq_hist["pos_90plus_dpd"] / dq_hist["pos_61_90dpd"].shift(1)).tail(6).mean()
            write_off_rate = ((dq_hist["cumulative_default_amount"].diff())
                               / dq_hist["pos_90plus_dpd"].shift(1)).tail(6).mean()

            deal_state = dict(
                beginning_pos=float(mis_hist.iloc[-1]["closing_pos_investor_share"]),
                scheduled_rate=float(latest_trend["scheduled_rate_trailing"] or 0),
                smm=float(latest_trend["smm_trailing"] or 0),
                ce_balance=float(wf_hist.iloc[-1]["ce_closing_balance"] or 0),
                pool_total=float(latest_dq["total_pool_outstanding"] or 0),
                dpd_fractions=tuple(float(latest_dq[c]) if pd.notna(latest_dq[c]) else 0.0
                                     for c in ["f0", "f1", "f2", "f3", "f4"]),
                roll_rates=tuple(float(r) if pd.notna(r) else 0.0 for r in (r01, r12, r23, r34)),
                write_off_rate=float(write_off_rate) if pd.notna(write_off_rate) else 0.0,
                tranche="Senior",
            )

            st.markdown("##### Scenario")
            c1, c2, c3 = st.columns(3)
            with c1:
                stress_factor = st.select_slider("Delinquency stress (× current 90+ roll rates)",
                                                   options=[1.0, 1.5, 2.0, 3.0], value=1.0)
            with c2:
                prepay_shock = st.slider("Prepayment shock (CPR, percentage points)",
                                           -10.0, 10.0, 0.0, 0.5)
            with c3:
                horizon = st.select_slider("Projection horizon (months)", options=[12, 24, 36], value=24)

            base_df, base_summary = stress_engine.run_stress_scenario(deal_state, 1.0, 0.0, horizon)
            scenario_df, scenario_summary = stress_engine.run_stress_scenario(
                deal_state, stress_factor, prepay_shock, horizon
            )

            st.divider()
            threshold = st.slider("Flag threshold (CE cover ratio, x)", 0.0, 3.0, 1.0, 0.1, key="stress_threshold")

            compare_df = pd.concat([
                base_df.assign(scenario="Base case (no stress)"),
                scenario_df.assign(scenario=f"{stress_factor:.1f}× delinquency, {prepay_shock:+.1f}pp CPR"),
            ])
            fig = px.line(compare_df, x="month_index", y="ce_cover_ratio", color="scenario", markers=True,
                          color_discrete_map={"Base case (no stress)": TEAL})
            fig.add_hline(y=threshold, line_dash="dash", line_color=CORAL,
                          annotation_text=f"Threshold {threshold:.1f}x", annotation_position="top left")
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=380,
                               xaxis_title="Month (projected)", yaxis_title="CE cover ratio (x)",
                               legend_title="")
            st.plotly_chart(fig, use_container_width=True)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Base WAL", f"{base_summary['wal_years']:.2f} yrs" if base_summary["wal_years"] else "—")
            c2.metric("Stressed WAL", f"{scenario_summary['wal_years']:.2f} yrs" if scenario_summary["wal_years"] else "—",
                      delta=(f"{scenario_summary['wal_years'] - base_summary['wal_years']:+.2f} yrs"
                             if base_summary["wal_years"] and scenario_summary["wal_years"] else None))
            c3.metric("CE depletion month (base)",
                      base_summary["ce_depleted_month"] or f"Not within {horizon}mo")
            c4.metric("CE depletion month (stressed)",
                      scenario_summary["ce_depleted_month"] or f"Not within {horizon}mo")

            # Plain-language auto-summary
            deal_display_name = deal_label_map[stress_deal_id]
            lines = []
            if scenario_summary["ce_depleted_month"]:
                lines.append(
                    f"Under a {stress_factor:.1f}× delinquency stress with a {prepay_shock:+.1f}pp CPR shock, "
                    f"**{deal_display_name}**'s credit enhancement is projected to be **fully depleted by month "
                    f"{scenario_summary['ce_depleted_month']}** of the {horizon}-month projection."
                )
            else:
                lines.append(
                    f"Under a {stress_factor:.1f}× delinquency stress with a {prepay_shock:+.1f}pp CPR shock, "
                    f"**{deal_display_name}**'s credit enhancement is **not** projected to fully deplete "
                    f"within {horizon} months (ending cover ratio "
                    f"{scenario_summary['final_ce_cover_ratio']:.2f}x)." if scenario_summary["final_ce_cover_ratio"] is not None
                    else f"CE is not projected to deplete within {horizon} months."
                )
            if scenario_summary["payout_impacted_month"]:
                lines.append(
                    f"Senior tranche payout is projected to be **impacted starting month "
                    f"{scenario_summary['payout_impacted_month']}**, once CE can no longer fully cover that "
                    f"month's write-offs."
                )
            else:
                lines.append("Senior tranche payout is not projected to be impacted within the horizon.")
            wal_shift = (scenario_summary["wal_years"] - base_summary["wal_years"]
                         if base_summary["wal_years"] and scenario_summary["wal_years"] else None)
            if wal_shift is not None:
                direction = "longer" if wal_shift > 0 else "shorter"
                lines.append(f"WAL shifts **{abs(wal_shift):.2f} years {direction}** than the base case "
                             f"({base_summary['wal_years']:.2f}y → {scenario_summary['wal_years']:.2f}y).")
            st.markdown(f'<div class="exec-narrative">{" ".join(lines)}</div>', unsafe_allow_html=True)

            with st.expander("Current-state inputs used for this projection (for transparency)"):
                st.json({k: (list(v) if isinstance(v, tuple) else v) for k, v in deal_state.items()})

# ==============================================================
# PAGE 7: FUNDING & ALM
# All-in-cost math lives in calc.deal_funding_cost (sql/07_schema_funding.sql)
# -- SQL-only, since it doesn't need WAL. The WAL-vs-tenor ALM check DOES
# need WAL, so that half is computed here in Python via risk_metrics.py,
# same pattern as the Credit Performance and Stress Testing pages.
# ==============================================================
elif page == "Funding & ALM":
    page_header("Funding cost & ALM — all-in cost vs alternatives, WAL vs stated tenor",
                "See sql/07_schema_funding.sql for the all-in-cost methodology and its "
                "stated-tenor-vs-WAL caveat.")

    st.subheader("All-in cost of funds")
    st.caption("All-in cost = investor payout rate + servicing fee + one-time execution costs "
               "amortized straight-line over the deal's STATED tenor (not actual WAL — see the "
               "SQL file's assumption #3 for why that understates the true effective cost).")

    funding_df = q("SELECT * FROM calc.deal_funding_cost")
    if funding_df.empty:
        st.info("No data in calc.deal_funding_cost yet — run generate_synthetic_data.py / "
                "load_to_postgres.py after applying sql/07_schema_funding.sql.")
    else:
        # Illustrative-only alternative funding cost benchmarks. NOT live
        # market data and NOT IIFL's actual borrowing cost on any facility
        # -- typical indicative levels for an NBFC of this profile, for a
        # demo comparison only.
        ILLUSTRATIVE_ALTERNATIVES = {
            "NCD (~3yr, AA-rated)": 8.75,
            "Term loan (working capital)": 9.75,
            "Commercial paper (<1yr)": 7.50,
        }
        st.caption("⚠️ NCD / term loan / CP figures below are **illustrative, typical-market placeholders** "
                   "— not live rates and not IIFL's actual cost of funds on any real facility.")

        valid = funding_df.dropna(subset=["all_in_cost_pct", "amount_securitised_deal_date"])
        weighted_all_in = (
            (valid["all_in_cost_pct"] * valid["amount_securitised_deal_date"]).sum()
            / valid["amount_securitised_deal_date"].sum()
        ) if not valid.empty else None

        compare_rows = [{"label": "Securitisation — portfolio weighted avg all-in cost", "rate": weighted_all_in}]
        compare_rows += [{"label": k, "rate": v} for k, v in ILLUSTRATIVE_ALTERNATIVES.items()]
        compare_df = pd.DataFrame(compare_rows)
        fig = px.bar(compare_df, x="label", y="rate", color="label",
                     color_discrete_sequence=[NAVY, "#8FA6B3", "#8FA6B3", "#8FA6B3"])
        fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=340,
                           xaxis_title="", yaxis_title="Rate (%)")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("###### By product / instrument")
        by_product = valid.groupby(["underlying", "instrument_type"]).apply(
            lambda g: pd.Series({
                "weighted_all_in_cost_pct": (g["all_in_cost_pct"] * g["amount_securitised_deal_date"]).sum()
                                             / g["amount_securitised_deal_date"].sum(),
                "deal_count": len(g),
            }), include_groups=False
        ).reset_index()
        st.dataframe(by_product, use_container_width=True)

        with st.expander("Per-deal detail"):
            st.dataframe(
                funding_df[["deal_name", "investor_name", "instrument_type", "underlying",
                            "investor_payout_rate", "servicing_fee_pct", "upfront_cost_amortized_pct",
                            "all_in_cost_pct", "stated_tenor_months"]],
                use_container_width=True,
            )

    st.divider()
    st.subheader("WAL vs stated tenor — ALM mismatch check")
    st.caption("WAL here is the same trailing closed-form approximation used on the Credit Performance "
               "page (risk_metrics.py) — a real ALM check would use full projected cash flows, not this.")
    st.warning("⚠️ **Known dataset limitation**: this generator's scheduled amortization rate isn't tied "
               "to product or stated tenor, so longer-stated-tenor products (HCF, LAP) will show up as "
               "systematically \"mismatched\" here — that's a gap in the synthetic data, not a real ALM "
               "signal on those specific deals. See sql/07_schema_funding.sql assumption #5.")

    smm_all = q("""
        SELECT s.deal_id, s.month, s.beginning_pos, s.scheduled_principal_payout, s.smm
        FROM calc.deal_smm_cpr s
        JOIN raw.deals d ON d.deal_id = s.deal_id
        WHERE d.stated_tenor_months IS NOT NULL
        ORDER BY s.deal_id, s.month
    """)
    if smm_all.empty:
        st.info("No SMM/CPR history available yet to compute WAL for the ALM check.")
    else:
        wal_trend_all = risk_metrics.add_rolling_cpr_wal(smm_all)
        latest_wal = wal_trend_all.sort_values("month").groupby("deal_id").tail(1)[["deal_id", "wal_years"]]

        deal_tenor = q("""
            SELECT deal_id, deal_name, investor_name, instrument_type, underlying, stated_tenor_months
            FROM raw.deals WHERE stated_tenor_months IS NOT NULL
        """)
        alm_df = deal_tenor.merge(latest_wal, on="deal_id", how="inner").dropna(subset=["wal_years"])
        alm_df["stated_tenor_years"] = alm_df["stated_tenor_months"] / 12.0
        alm_df["wal_minus_tenor_years"] = alm_df["wal_years"] - alm_df["stated_tenor_years"]

        if alm_df.empty:
            st.info("Not enough deals with both a WAL estimate and a stated tenor yet.")
        else:
            mismatch_threshold = st.slider("Flag if |WAL − stated tenor| exceeds (years)", 0.25, 5.0, 1.0, 0.25)
            alm_df["mismatched"] = alm_df["wal_minus_tenor_years"].abs() > mismatch_threshold

            fig2 = px.scatter(alm_df, x="stated_tenor_years", y="wal_years", color="mismatched",
                               hover_data=["deal_name", "investor_name", "instrument_type"],
                               color_discrete_map={True: CORAL, False: TEAL})
            max_axis = max(alm_df["stated_tenor_years"].max(), alm_df["wal_years"].max()) * 1.05
            fig2.add_shape(type="line", x0=0, y0=0, x1=max_axis, y1=max_axis,
                           line=dict(color=SLATE, dash="dot"))
            fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=420,
                               xaxis_title="Stated tenor (years)", yaxis_title="WAL (years, trailing estimate)",
                               legend_title="Mismatched")
            st.plotly_chart(fig2, use_container_width=True)

            mismatched_df = alm_df.loc[alm_df["mismatched"]].sort_values(
                "wal_minus_tenor_years", key=abs, ascending=False
            )
            st.markdown(f"###### {len(mismatched_df)} of {len(alm_df)} deals flagged (threshold: "
                        f"{mismatch_threshold:.2f} years)")
            if not mismatched_df.empty:
                st.dataframe(
                    mismatched_df[["deal_name", "investor_name", "instrument_type", "underlying",
                                   "stated_tenor_years", "wal_years", "wal_minus_tenor_years"]],
                    use_container_width=True,
                )

# ==============================================================
# PAGE 8: REGULATORY COMPLIANCE
# A simple compliance SUMMARY, deliberately not a rules engine -- see
# sql/08_schema_regulatory.sql for what each check does and doesn't
# capture (especially the MHP simplification and the true-sale note's
# "assumption, not legal opinion" caveat).
# ==============================================================
elif page == "Regulatory Compliance":
    page_header("Regulatory markers — MRR, MHP, true-sale assumptions",
                "A simple compliance summary for a human to review, not an automated rules engine. "
                "See sql/08_schema_regulatory.sql for every simplification made.")

    reg_df = q("SELECT * FROM calc.regulatory_compliance_summary")
    if reg_df.empty:
        st.info("No data in calc.regulatory_compliance_summary yet — run generate_synthetic_data.py "
                "/ load_to_postgres.py after applying sql/08_schema_regulatory.sql.")
    else:
        st.subheader("MRR (Minimum Retention Requirement)")
        st.caption("Threshold is adjustable here rather than fixed, per the Phase 5 requirement — "
                   "calc.mrr_compliance (Phase 1) still exists as a fixed-5% reference view.")
        mrr_threshold = st.select_slider("MRR compliance threshold", options=[0.05, 0.10], value=0.05,
                                           format_func=lambda x: f"{x:.0%}")
        reg_df["mrr_compliance_status"] = reg_df["mrr_percent"].apply(
            lambda x: "Unknown" if pd.isna(x) else ("Compliant" if x >= mrr_threshold else "Non-Compliant")
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Compliant", int((reg_df["mrr_compliance_status"] == "Compliant").sum()))
        c2.metric("Non-Compliant", int((reg_df["mrr_compliance_status"] == "Non-Compliant").sum()))
        c3.metric("Total deals", len(reg_df))
        mrr_non_compliant = reg_df.loc[reg_df["mrr_compliance_status"] == "Non-Compliant"]
        if not mrr_non_compliant.empty:
            with st.expander(f"{len(mrr_non_compliant)} non-compliant deal(s)"):
                st.dataframe(
                    mrr_non_compliant[["deal_name", "investor_name", "instrument_type", "mrr_percent"]],
                    use_container_width=True,
                )

        st.divider()
        st.subheader("MHP (Minimum Holding Period)")
        st.caption("Required MHP is an ILLUSTRATIVE simplification (≤24mo stated tenor → 3 months, "
                   "otherwise → 6 months) of RBI's actual tenor-and-repayment-frequency matrix — "
                   "see sql/08_schema_regulatory.sql assumption #2. Not a verbatim reproduction of the regulation.")
        mhp_counts = reg_df["mhp_compliance_status"].value_counts()
        c1, c2, c3 = st.columns(3)
        c1.metric("Compliant", int(mhp_counts.get("Compliant", 0)))
        c2.metric("Non-Compliant", int(mhp_counts.get("Non-Compliant", 0)))
        c3.metric("Unknown", int(mhp_counts.get("Unknown", 0)))
        mhp_non_compliant = reg_df.loc[reg_df["mhp_compliance_status"] == "Non-Compliant"]
        if not mhp_non_compliant.empty:
            mhp_detail = q("""
                SELECT deal_name, investor_name, instrument_type, stated_tenor_months,
                       underlying_seasoning_months
                FROM calc.mhp_compliance WHERE mhp_compliance_status = 'Non-Compliant'
            """)
            with st.expander(f"{len(mhp_non_compliant)} non-compliant deal(s)"):
                st.dataframe(mhp_detail, use_container_width=True)

        st.divider()
        st.subheader("True-sale assumptions")
        st.caption("⚠️ These are this project's ASSUMPTIONS about true-sale status, not a legal "
                   "determination — a real true-sale opinion requires actual review of the transfer "
                   "documents by counsel. See sql/08_schema_regulatory.sql assumption #4.")
        unverified = reg_df.loc[reg_df["true_sale_criteria_met"] == False]
        c1, c2 = st.columns(2)
        c1.metric("Assumed true sale", int((reg_df["true_sale_criteria_met"] == True).sum()))
        c2.metric("Flagged — not independently verified", len(unverified))
        if not unverified.empty:
            with st.expander(f"{len(unverified)} deal(s) flagged for legal review"):
                st.dataframe(
                    unverified[["deal_name", "investor_name", "instrument_type", "true_sale_assumption_note"]],
                    use_container_width=True,
                )

        st.divider()
        st.subheader("Combined compliance summary")
        summary_df = reg_df.copy()
        summary_df["overall_flag"] = (
            (summary_df["mrr_compliance_status"] == "Non-Compliant")
            | (summary_df["mhp_compliance_status"] == "Non-Compliant")
            | (summary_df["true_sale_criteria_met"] == False)
        )
        st.caption(f"{int(summary_df['overall_flag'].sum())} of {len(summary_df)} deals have at least "
                   f"one open flag (MRR, MHP, or true-sale).")
        st.dataframe(
            summary_df[["deal_name", "investor_name", "instrument_type", "mrr_compliance_status",
                        "mhp_compliance_status", "true_sale_criteria_met", "overall_flag"]]
            .sort_values("overall_flag", ascending=False),
            use_container_width=True,
        )

# ==============================================================
# PAGE 9: UPLOAD MONTHLY MIS (admin-only)
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
# PAGE 10: USER MANAGEMENT (admin-only)
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