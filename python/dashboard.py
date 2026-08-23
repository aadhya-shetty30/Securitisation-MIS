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
# Theme: IIFL Finance-inspired palette -- deep navy blue (their
# primary brand color) with warm gold/amber accent (echoes their
# logo's gold-loan branding). Note: exact hex values below are a
# close approximation, not pixel-sampled from the live site --
# swap them for the exact brand hex codes if you have their
# style guide during the internship.
# ------------------------------------------------------------
NAVY = "#0B2545"       # IIFL-style deep navy -- headings, primary text
SLATE = "#5C7184"      # secondary text
GOLD = "#D4A017"       # IIFL-style gold accent -- primary highlight (Gold product, positive)
TEAL = "#1B6E7A"       # secondary accent (payout, DA)
CORAL = "#C0392B"      # NPA / risk accent -- deeper red, closer to IIFL's warning tone
CARD_BG = "#FAF7F0"    # warm ivory KPI card background (less flat than plain gray)
BORDER = "#D8CFB8"     # warm-toned card / divider borders
CHART_SEQ = ["#D4A017", "#0B2545", "#1B6E7A", "#C0392B", "#8FA6B3", "#B9CFC0"]

px.defaults.color_discrete_sequence = CHART_SEQ
px.defaults.template = "plotly_white"

st.markdown(f"""
<style>
    .stApp {{ background-color: #FCFDFE; }}
    section[data-testid="stSidebar"] {{
        background-color: {NAVY};
    }}
    section[data-testid="stSidebar"] * {{ color: #EAF1F5 !important; }}
    section[data-testid="stSidebar"] .stRadio > label {{ color: #EAF1F5 !important; }}
    div[data-testid="stMetric"] {{
        background-color: {CARD_BG};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 14px 16px 10px 16px;
    }}
    div[data-testid="stMetricLabel"] {{ color: {SLATE} !important; font-weight: 500; }}
    div[data-testid="stMetricValue"] {{
        color: {NAVY} !important;
        white-space: nowrap;
        overflow: visible;
        font-size: 1.5rem !important;
    }}
    h1 {{ color: {NAVY}; font-weight: 600; }}
    h2, h3 {{ color: {NAVY}; font-weight: 500; }}
    .exec-narrative {{
        background-color: #FFF8E7;
        border-left: 4px solid {GOLD};
        border-radius: 6px;
        padding: 16px 20px;
        color: {NAVY};
        font-size: 0.95rem;
        line-height: 1.6;
    }}
    div[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 8px; }}
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


@st.cache_resource
def get_conn():
    return psycopg2.connect(DB_URL)


def q(sql, params=None):
    return pd.read_sql(sql, get_conn(), params=params)


# ------------------------------------------------------------
# Sidebar: global filters
# ------------------------------------------------------------
st.sidebar.markdown("## NBFC Securitisation MIS")
st.sidebar.caption("Treasury & DA management dashboard")
st.sidebar.markdown("---")
page = st.sidebar.radio("View", ["Management Summary", "Executive Review", "Data Quality"])

months = q("SELECT DISTINCT month FROM raw.monthly_mis ORDER BY month DESC")["month"].tolist()
investors = ["All Investors"] + sorted(
    q("SELECT DISTINCT investor_name FROM raw.deals ORDER BY investor_name")["investor_name"].tolist()
)

# ==============================================================
# PAGE 1: MANAGEMENT SUMMARY
# ==============================================================
if page == "Management Summary":
    st.title("Investor-wise Securitisation & DA Management View")
    st.caption("Live from the calculation layer — select an investor and month to filter every figure below.")

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
    st.title("Bank relationship — consolidated executive review")
    st.caption("Portfolio KPIs, financial-year and quarter-on-quarter performance, and an auto-generated summary.")

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
    st.subheader("Table 1 — Financial Year-Wise Performance")
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

    st.subheader("Table 2 — Quarter-on-Quarter Performance")
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
else:
    st.title("Data Quality Overview")
    st.caption(
        "Mirrors the original workbook's explicit distinction between missing and zero data "
        "(e.g. deleted payout source columns were shown as unavailable, never zero)."
    )

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
