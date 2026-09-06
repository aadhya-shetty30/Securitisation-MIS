"""
Step 5 -- Synthetic data generator for the NBFC PTC/DA securitisation MIS project.

Produces five CSVs matching the raw schema in sql/01_schema_raw.sql,
sql/05_schema_waterfall.sql, and sql/06_schema_delinquency.sql:
    master_code.csv
    deals.csv
    monthly_mis.csv
    monthly_waterfall.csv
    monthly_delinquency.csv

Design choices (documented, not hidden):
- 300 deals, borrowing dates spread Apr-2024 through Jun-2026.
- Monthly MIS history spans Apr-2024 through Jul-2026 (28 months).
- A deal only gets monthly_mis rows from its borrowing month onward,
  and NONE after its closure month if status = 'Closed' -- this is what
  creates realistic data-quality gaps (Missing, not Zero) for the
  calc layer to demonstrate, mirroring the real workbook's explicit
  "unavailable vs zero" distinction.
- POS amortizes down over time with small random noise; payout is
  derived from POS and ROI, not independently randomized, so the
  numbers are internally consistent (a payout much larger than
  outstanding POS would be an obvious synthetic-data smell).
- ~4% of deal-months are deliberately marked 'Missing' or 'Incomplete'
  to give the dashboard's data-quality view something real to show.
- PTC deals now also carry a tranche (Senior/Subordinate), a dominant
  credit-enhancement type, and a monthly waterfall + CE roll-forward.
  See sql/05_schema_waterfall.sql for the full list of TO BE CONFIRMED
  structural assumptions this generator encodes -- they are not repeated
  in full here, only where the code makes a specific numeric choice.
- Every deal is assigned a hidden "credit trajectory" (Clean /
  Deteriorating / Recovering) that drives a simple Markov-style DPD
  bucket roll-forward month over month, instead of independent random
  noise per month -- so a deal's delinquency actually trends rather than
  jittering. See sql/06_schema_delinquency.sql for the full list of
  TO BE CONFIRMED assumptions in that layer (roll-rate/CPR conventions,
  cure/write-off simplifications, etc).
- total_principal_payout is now scheduled_principal_payout (based on the
  deal's declining balance, fixed from a Phase 1 inconsistency where it
  was flat off the original amount) PLUS a modeled prepayment component
  -- this is what makes CPR/SMM computable at all.
- Every deal now carries a stated_tenor_months (Phase 4), and all 7
  execution-cost components are scaled to amount_securitised instead of
  5 of them being fixed absolute Rs Crore ranges -- the fixed ranges were
  a latent bug exposed by Phase 1's small Subordinate-tranche deals
  (a Rs 2-8 Cr rating fee on a Rs 2 Cr deal is not a sane number), fixed
  once Phase 4's all-in-cost view made it visible as a percentage.
"""

import csv
import os
import random
from datetime import date
from dateutil.relativedelta import relativedelta

random.seed(42)  # reproducible dataset

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

N_DEALS = 300
MIS_START = date(2024, 4, 1)
MIS_END = date(2026, 7, 1)

INVESTORS = [
    "Punjab and Sind Bank", "Aditya Birla Capital Limited", "Bank of Maharashtra",
    "Karur Vysya Bank", "South Indian Bank", "IDBI Bank", "Federal Bank",
    "Central Bank of India", "UCO Bank", "Indian Overseas Bank",
    "DCB Bank", "RBL Bank", "Bandhan Bank", "AU Small Finance Bank",
]
PRODUCTS = ["Gold", "SME", "MFI", "LAP", "BL", "HCF"]
INSTRUMENTS = ["PTC", "DA"]
RATINGS = ["AAA(SO)", "AA+(SO)", "AA(SO)", "A+(SO)", "Unrated"]
TRUSTEES = ["Catalyst Trusteeship", "IDBI Trusteeship", "Vistra ITCL", "Axis Trustee"]

# Phase 2: DPD bucket roll-forward parameters per credit trajectory
# archetype. Monthly probabilities of a bucket's balance rolling forward
# one bucket, curing straight back to 0-DPD, or (for the 90+ bucket)
# being written off. TO BE CONFIRMED / illustrative only -- not fitted to
# any real static-pool study; picked to produce plausible-looking curves
# for a demo (see sql/06_schema_delinquency.sql assumption #3).
DPD_TRAJECTORY_PARAMS = {
    "Clean": dict(roll_0_1=0.015, roll_1_2=0.22, roll_2_3=0.22, roll_3_4=0.28,
                   cure_1=0.55, cure_2=0.35, cure_3=0.15, write_off=0.05),
    "Deteriorating": dict(roll_0_1=0.045, roll_1_2=0.42, roll_2_3=0.42, roll_3_4=0.48,
                           cure_1=0.18, cure_2=0.08, cure_3=0.04, write_off=0.10),
}
CREDIT_TRAJECTORY_WEIGHTS = {"Clean": 0.70, "Deteriorating": 0.15, "Recovering": 0.15}

# Phase 4: stated tenor at issuance, in months -- a NEW synthetic field
# (the schema never tracked this before). Ranges are illustrative,
# loosely product-shaped (short-tenor gold loans, longer LAP/HCF), NOT
# fitted to real IIFL deal terms -- see sql/07_schema_funding.sql
# assumption #1.
STATED_TENOR_MONTHS_RANGE = {
    "Gold": (6, 12), "MFI": (12, 24), "BL": (12, 24),
    "SME": (24, 48), "LAP": (36, 72), "HCF": (48, 84),
}

# Phase 5: true-sale assumption notes -- NOT a legal determination, see
# sql/08_schema_regulatory.sql assumption #4.
TRUE_SALE_NOTES_PTC = [
    "Assumed true sale: SPV holds legal title to the pool; no recourse to "
    "originator beyond the stated credit enhancement.",
    "Assumed true sale: originator retains servicing rights only, no "
    "equitable interest retained beyond MRR.",
]
TRUE_SALE_NOTES_DA = [
    "Assumed true sale: outright assignment of receivables; assignee "
    "holds pro-rata beneficial interest in the underlying loans.",
]
TRUE_SALE_NOTE_UNVERIFIED = (
    "True-sale status not independently verified for this synthetic deal "
    "-- flagged for legal review."
)

def month_range(start, end):
    months = []
    cur = start
    while cur <= end:
        months.append(cur)
        cur += relativedelta(months=1)
    return months

ALL_MONTHS = month_range(MIS_START, MIS_END)


def fy_and_quarter(d: date):
    # Indian FY: Apr-Mar. FY26 = Apr-2025 to Mar-2026.
    fy_start_year = d.year if d.month >= 4 else d.year - 1
    fy_label = f"FY{str(fy_start_year + 1)[-2:]}"
    q = ((d.month - 4) % 12) // 3 + 1
    return fy_label, f"{fy_label} Q{q}"


def gen_master_code_and_deals():
    master_rows = []
    deal_rows = []

    for i in range(1, N_DEALS + 1):
        deal_id = f"D{i:04d}"
        instrument = random.choice(INSTRUMENTS)
        product = random.choice(PRODUCTS)
        investor = random.choice(INVESTORS)
        company = "IIFL Finance Limited" if random.random() > 0.05 else "IIFL Samasta Finance"

        # Borrowing date: spread across Apr-24 to Jun-26 so deals originate at different times
        max_offset_months = (MIS_END.year - MIS_START.year) * 12 + (MIS_END.month - MIS_START.month)
        offset = random.randint(0, max_offset_months - 1)  # leave room for at least 1 month of MIS
        borrowing_date = MIS_START + relativedelta(months=offset)
        borrowing_date = borrowing_date.replace(day=random.randint(1, 28))

        deal_name = f"{investor.split()[0]}-{product}-{deal_id}"

        # Status: deals originated more than ~15 months ago have a chance of being closed
        months_since_origination = (MIS_END.year - borrowing_date.year) * 12 + (MIS_END.month - borrowing_date.month)
        status = "Closed" if months_since_origination > 15 and random.random() < 0.35 else "Active"

        fy, period = fy_and_quarter(borrowing_date)

        deal_roi = round(random.uniform(9.0, 13.5), 2)
        spread = round(random.uniform(1.5, 3.5), 2)
        mclr_deal_date = round(deal_roi - spread, 2)
        mclr_linked = random.choice(["MCLR Linked", "Fixed"])

        amount_securitised = round(random.uniform(15, 350), 2)  # Rs Crs
        purchase_consideration = round(amount_securitised * random.uniform(0.92, 0.99), 2)
        no_of_contracts = random.randint(500, 25000)

        first_loss_pct = round(random.uniform(0.03, 0.12), 4)
        overcollateral_pct = round(random.uniform(0.0, 0.08), 4)
        rating = random.choice(RATINGS)

        # --- Phase 1: tranche + credit enhancement -----------------------
        # Tranche: PTC deals are modeled as a single tranche per row (see
        # sql/05_schema_waterfall.sql assumption #2 re: no shared pool_id
        # linking a paired senior/subordinate structure). Most PTCs here
        # are senior-only; ~15% are generated as a subordinate/junior
        # tranche with a correspondingly smaller notional below.
        if instrument == "PTC":
            tranche = random.choices(["Senior", "Subordinate"], weights=[0.85, 0.15])[0]
        else:
            tranche = None  # DA -- no tranching

        # Subordinate tranches are sized as a small slice, not the full
        # pool -- reflects that a junior piece is typically 5-15% of the
        # equivalent senior notional, even though (per assumption #2) we
        # aren't literally deriving it from a linked senior deal here.
        if tranche == "Subordinate":
            amount_securitised = round(random.uniform(1, 25), 2)
            purchase_consideration = round(amount_securitised * random.uniform(0.92, 0.99), 2)

        # Credit enhancement type: one dominant mechanism per deal.
        # DA deals default to 'None' -- see assumption #3 (formal tranched
        # CE is a PTC-market feature; treated as a simplification for DA).
        if instrument == "PTC":
            ce_type = random.choices(
                ["Cash Collateral", "Overcollateralization", "First Loss Guarantee", "None"],
                weights=[0.35, 0.30, 0.25, 0.10],
            )[0]
        else:
            ce_type = "None"

        # Make the legacy pct columns consistent with the dominant CE type
        # instead of letting both be independently non-zero (see
        # sql/05_schema_waterfall.sql assumption #3). Cash Collateral has
        # no pre-existing pct column, so a fresh one is generated for it
        # and used only to size credit_enhancement_initial_amount.
        if ce_type == "Overcollateralization":
            first_loss_pct = 0.0
            ce_initial_amount = round(overcollateral_pct * amount_securitised, 2)
        elif ce_type == "First Loss Guarantee":
            overcollateral_pct = 0.0
            ce_initial_amount = round(first_loss_pct * amount_securitised, 2)
        elif ce_type == "Cash Collateral":
            first_loss_pct = 0.0
            overcollateral_pct = 0.0
            cash_collateral_pct = round(random.uniform(0.03, 0.08), 4)
            ce_initial_amount = round(cash_collateral_pct * amount_securitised, 2)
        else:  # None
            first_loss_pct = 0.0
            overcollateral_pct = 0.0
            ce_initial_amount = 0.0

        # MRR (Minimum Retention Requirement): TO BE CONFIRMED -- real RBI
        # tiers (5%/10%) depend on loan tenor, which this schema doesn't
        # track. Modeled as a flat random pick between the two common
        # thresholds rather than derived from tenor. Applies to both PTC
        # and DA (RBI's MRR requirement is not PTC-only).
        mrr_percent = random.choices([0.05, 0.10], weights=[0.6, 0.4])[0]

        # Phase 2: hidden credit trajectory driving the DPD roll-forward
        # in gen_monthly_delinquency -- not persisted to any DB column,
        # it's a generator-only device (see DPD_TRAJECTORY_PARAMS above).
        credit_trajectory = random.choices(
            list(CREDIT_TRAJECTORY_WEIGHTS.keys()), weights=list(CREDIT_TRAJECTORY_WEIGHTS.values())
        )[0]
        # "Recovering" deals behave like Deteriorating for a while, then
        # switch to Clean-like parameters -- the random recovery month is
        # fixed per deal so the switch is a one-time trend, not noise.
        recovery_month = random.randint(6, 14) if credit_trajectory == "Recovering" else None

        # Phase 4: stated tenor at issuance (see STATED_TENOR_MONTHS_RANGE above).
        tenor_lo, tenor_hi = STATED_TENOR_MONTHS_RANGE[product]
        stated_tenor_months = random.randint(tenor_lo, tenor_hi)

        # Phase 5: MHP seasoning -- illustrative required MHP by stated
        # tenor bucket (see sql/08_schema_regulatory.sql assumption #2),
        # with a deliberate ~15% non-compliant slice so the check has
        # something real to flag.
        required_mhp = 3 if stated_tenor_months <= 24 else 6
        if random.random() < 0.85:
            underlying_seasoning_months = random.randint(required_mhp, required_mhp + 12)
        else:
            underlying_seasoning_months = random.randint(0, max(required_mhp - 1, 0))

        # Phase 5: true-sale assumption -- not a legal determination, see
        # sql/08_schema_regulatory.sql assumption #4.
        if random.random() < 0.95:
            true_sale_criteria_met = True
            true_sale_note = random.choice(TRUE_SALE_NOTES_PTC if instrument == "PTC" else TRUE_SALE_NOTES_DA)
        else:
            true_sale_criteria_met = False
            true_sale_note = TRUE_SALE_NOTE_UNVERIFIED

        master_rows.append({
            "company": company, "instrument": instrument,
            "deal_name": deal_name, "code": deal_id, "status": status,
        })

        deal_rows.append({
            "deal_id": deal_id, "sub_code": "", "deal_name": deal_name, "status": status,
            "fy": fy, "period": period, "company": company,
            "instrument_type": instrument, "underlying": product,
            "psl_npsl": random.choice(["PSL", "NPSL"]),
            "investor_name": investor,
            "trustee_ar": random.choice(TRUSTEES), "trustee": random.choice(TRUSTEES),
            "ratio": round(random.uniform(0.85, 0.95), 4),
            "borrowing_date": borrowing_date.isoformat(),
            "payout_date": (borrowing_date + relativedelta(months=1)).isoformat(),
            "mclr_linked_or_fixed": mclr_linked,
            "mclr_on_deal_date": mclr_deal_date,
            "spread": spread, "deal_roi": deal_roi,
            "servicing_fee_pct": round(random.uniform(0.5, 2.0), 4),
            "net_roi": round(deal_roi - random.uniform(0.5, 1.5), 4),
            "no_of_pool_contracts": no_of_contracts,
            "amount_securitised_deal_date": amount_securitised,
            "purchase_consideration_deal_date": purchase_consideration,
            "rating": rating, "loss_estimation_rating": rating,
            "first_loss_facility_pct": first_loss_pct,
            "overcollateral_pct": overcollateral_pct,
            "tranche": tranche if tranche else "",
            "credit_enhancement_type": ce_type,
            "credit_enhancement_initial_amount": ce_initial_amount,
            "mrr_percent": mrr_percent,
            "stated_tenor_months": stated_tenor_months,
            "underlying_seasoning_months": underlying_seasoning_months,
            "true_sale_criteria_met": true_sale_criteria_met,
            "true_sale_assumption_note": true_sale_note,
            # Phase 4 fix: all 7 execution-cost components are now scaled
            # to amount_securitised (bps-of-deal-size), not fixed absolute
            # Rs Crore ranges. The previous fixed ranges (e.g.
            # documentation_charges = uniform(0.5, 3)) were calibrated for
            # the original 15-350 Cr deal range and were harmless while
            # "Total Deal Execution Expenses" was only ever shown as an
            # absolute Rs Crore figure. Phase 1's Subordinate tranches
            # (Rs 1-25 Cr) exposed the bug once Phase 4 expressed the same
            # costs as a % of deal size: a fixed Rs 2-8 Cr rating fee on a
            # Rs 2 Cr subordinate deal is a 100-400% "cost," which is
            # obviously wrong. Ranges below are illustrative bps-of-deal-
            # size, not fitted to real IIFL fee schedules -- TO BE
            # CONFIRMED -- but at least internally consistent across deal
            # sizes now.
            "processing_fees_paid": round(amount_securitised * 0.001, 2),
            "documentation_charges": round(amount_securitised * random.uniform(0.0005, 0.0015), 2),
            "external_ca_fees": round(amount_securitised * random.uniform(0.001, 0.003), 2),
            "legal_documentation_fees": round(amount_securitised * random.uniform(0.001, 0.0025), 2),
            "rating_fees": round(amount_securitised * random.uniform(0.0015, 0.004), 2),
            "trustee_fees_pa": round(amount_securitised * random.uniform(0.0005, 0.0015), 2),
            "arranger_fees": round(amount_securitised * 0.0025, 2),
            "_borrowing_date_obj": borrowing_date,  # internal use for monthly_mis gen
            "_amount_securitised": amount_securitised,
            "_deal_roi": deal_roi,
            "_status": status,
            "_credit_trajectory": credit_trajectory,
            "_recovery_month": recovery_month,
        })

    return master_rows, deal_rows


def gen_monthly_mis(deal_rows):
    rows = []
    for d in deal_rows:
        borrowing = d["_borrowing_date_obj"]
        amount = d["_amount_securitised"]
        roi = d["_deal_roi"]
        status = d["_status"]

        deal_months = [m for m in ALL_MONTHS if m >= borrowing.replace(day=1)]
        if status == "Closed":
            # closed deals stop reporting somewhere before MIS_END
            max_active_months = random.randint(6, max(6, len(deal_months) - 1))
            deal_months = deal_months[:max_active_months]

        pos = amount  # starts at full amount securitised, amortizes down
        monthly_amort_rate = random.uniform(0.02, 0.05)  # 2-5% contractual runoff per month
        # Per-deal prepayment propensity (Phase 2) -- base monthly voluntary
        # prepay rate, before per-month noise and occasional lumpy events.
        prepay_base = random.uniform(0.0, 0.02)

        for idx, m in enumerate(deal_months):
            # Randomly create data-quality gaps (~4% of rows)
            roll = random.random()
            if roll < 0.02:
                data_status = "Missing"
            elif roll < 0.03:
                data_status = "Incomplete"
            elif roll < 0.04:
                data_status = "Reconciliation Required"
            else:
                data_status = "Available"

            beginning_pos = pos
            # Scheduled principal: FIXED in Phase 2 to be based on the
            # declining balance (beginning_pos), not the original deal
            # amount -- previously flat off `amount`, which didn't
            # reconcile with the geometrically-decaying POS it was meant
            # to fund (see sql/06_schema_delinquency.sql assumption #1).
            scheduled_principal = round(beginning_pos * monthly_amort_rate, 2)

            # Prepayment: noisy around the deal's base propensity, with an
            # occasional lumpy bullet prepayment (e.g. a borrower closing
            # out a loan early). Capped so total principal never exceeds
            # the beginning balance.
            prepay_pct = prepay_base * random.uniform(0.5, 1.5)
            if random.random() < 0.05:
                prepay_pct += random.uniform(0.01, 0.05)
            prepayment = beginning_pos * prepay_pct

            total_principal = min(scheduled_principal + prepayment, beginning_pos)
            prepayment = round(total_principal - scheduled_principal, 2)
            total_principal = round(total_principal, 2)

            pos = max(beginning_pos - total_principal, 0)

            npa_rate = random.uniform(0.0, 0.03) if random.random() > 0.9 else 0.0
            npa_amount = round(pos * npa_rate, 2)
            monthly_mclr = round(roi - random.uniform(1.5, 3.5), 4)
            monthly_roi = round(monthly_mclr + random.uniform(1.5, 3.5), 4)

            if data_status in ("Missing", "Incomplete"):
                # partial/blank row -- matches the real workbook's behavior of leaving
                # fields blank rather than zero when data wasn't reported
                rows.append({
                    "deal_id": d["deal_id"], "month": m.isoformat(),
                    "total_pool_outstanding": "", "closing_pos_investor_share": "",
                    "total_bank_payout": "", "total_principal_payout": "",
                    "scheduled_principal_payout": "",
                    "npa_amount": "", "mclr": "", "roi": "", "investor_share_x_roi": "",
                    "data_status": data_status,
                })
                continue

            payout = round(pos * (monthly_roi / 100) / 12, 2)

            rows.append({
                "deal_id": d["deal_id"], "month": m.isoformat(),
                "total_pool_outstanding": round(pos * random.uniform(1.0, 1.15), 2),
                "closing_pos_investor_share": round(pos, 2),
                "total_bank_payout": payout,
                "total_principal_payout": total_principal,
                "scheduled_principal_payout": scheduled_principal,
                "npa_amount": npa_amount,
                "mclr": monthly_mclr, "roi": monthly_roi,
                "investor_share_x_roi": round(pos * monthly_roi / 100, 2),
                "data_status": data_status,
            })
    return rows


def gen_monthly_delinquency(deal_rows, mis_rows):
    """DPD bucket roll-forward, one row per deal per month, for ALL
    instrument types (DPD is a pool asset-quality measure regardless of
    PTC/DA funding -- unlike monthly_waterfall, DA deals ARE included).

    Only 'Available' monthly_mis months are used, same gating as
    monthly_waterfall, so gaps line up with what the MIS itself reports.
    See sql/06_schema_delinquency.sql assumption #3 for the full list of
    simplifications in this roll-forward (cures jump straight to 0-DPD,
    write-offs are added back into 0-DPD to keep buckets summing to
    total_pool_outstanding, etc).
    """
    from collections import defaultdict

    deal_lookup = {d["deal_id"]: d for d in deal_rows}
    mis_by_deal = defaultdict(list)
    for r in mis_rows:
        if r["data_status"] == "Available":
            mis_by_deal[r["deal_id"]].append(r)

    rows = []
    for deal_id, mis_list in mis_by_deal.items():
        d = deal_lookup[deal_id]
        trajectory = d["_credit_trajectory"]
        recovery_month = d["_recovery_month"]

        f0, f1, f2, f3, f4 = 1.0, 0.0, 0.0, 0.0, 0.0
        cumulative_default = 0.0

        for month_idx, r in enumerate(sorted(mis_list, key=lambda x: x["month"])):
            active_archetype = trajectory
            if trajectory == "Recovering":
                active_archetype = "Deteriorating" if month_idx < recovery_month else "Clean"
            p = DPD_TRAJECTORY_PARAMS[active_archetype]
            pos_total = float(r["total_pool_outstanding"])

            move_0_to_1 = f0 * p["roll_0_1"]
            move_1_to_2 = f1 * p["roll_1_2"]
            cure_1_to_0 = f1 * p["cure_1"]
            move_2_to_3 = f2 * p["roll_2_3"]
            cure_2_to_0 = f2 * p["cure_2"]
            move_3_to_4 = f3 * p["roll_3_4"]
            cure_3_to_0 = f3 * p["cure_3"]
            write_off = f4 * p["write_off"]

            new_f0 = f0 - move_0_to_1 + cure_1_to_0 + cure_2_to_0 + cure_3_to_0 + write_off
            new_f1 = f1 - move_1_to_2 - cure_1_to_0 + move_0_to_1
            new_f2 = f2 - move_2_to_3 - cure_2_to_0 + move_1_to_2
            new_f3 = f3 - move_3_to_4 - cure_3_to_0 + move_2_to_3
            new_f4 = f4 - write_off + move_3_to_4

            total_frac = new_f0 + new_f1 + new_f2 + new_f3 + new_f4
            if total_frac > 0:
                f0, f1, f2, f3, f4 = (x / total_frac for x in (new_f0, new_f1, new_f2, new_f3, new_f4))
            else:
                f0, f1, f2, f3, f4 = 1.0, 0.0, 0.0, 0.0, 0.0

            cumulative_default += write_off * pos_total

            rows.append({
                "deal_id": deal_id, "month": r["month"],
                "pos_0dpd": round(f0 * pos_total, 2),
                "pos_1_30dpd": round(f1 * pos_total, 2),
                "pos_31_60dpd": round(f2 * pos_total, 2),
                "pos_61_90dpd": round(f3 * pos_total, 2),
                "pos_90plus_dpd": round(f4 * pos_total, 2),
                "cumulative_default_amount": round(cumulative_default, 2),
            })
    return rows


def gen_monthly_waterfall(deal_rows, mis_rows, delinquency_rows):
    """Long-format waterfall + CE roll-forward, one row per (PTC deal, month).

    Only PTC deals with a Senior/Subordinate tranche get rows -- DA deals
    are skipped entirely (assumption #9 in sql/05_schema_waterfall.sql).
    Only 'Available' monthly_mis months are used, so waterfall gaps line
    up with the same Missing/Incomplete months as monthly_mis rather than
    inventing waterfall data the MIS itself doesn't have.

    See sql/05_schema_waterfall.sql for the full assumptions list --
    notably #5 (priority order), #7 (no CE replenishment), and #8 (why
    ce_cover_ratio is blank for Subordinate rows). Assumption #6 (CE draw
    trigger) is now implemented using rising 90+ DPD from
    monthly_delinquency, per the Phase 2 requirement -- see the
    is_rough_month logic below.
    """
    from collections import defaultdict

    deal_lookup = {d["deal_id"]: d for d in deal_rows}
    mis_by_deal = defaultdict(list)
    for r in mis_rows:
        if r["data_status"] == "Available":
            mis_by_deal[r["deal_id"]].append(r)
    dpd_lookup = {(r["deal_id"], r["month"]): r for r in delinquency_rows}

    rows = []
    for deal_id, mis_list in mis_by_deal.items():
        d = deal_lookup[deal_id]
        if d["instrument_type"] != "PTC" or d["tranche"] not in ("Senior", "Subordinate"):
            continue  # DA deals, and any malformed row, excluded

        tranche = d["tranche"]
        servicing_fee_pct = float(d["servicing_fee_pct"] or 0)
        ce_balance = float(d["credit_enhancement_initial_amount"] or 0)
        prev_90plus_ratio = None

        for r in sorted(mis_list, key=lambda x: x["month"]):
            pos = float(r["closing_pos_investor_share"])
            interest = float(r["total_bank_payout"])
            principal = float(r["total_principal_payout"])
            pool_total = float(r["total_pool_outstanding"]) or 0.0

            servicing_fee_paid = round(pos * servicing_fee_pct / 100 / 12, 2)
            senior_interest_paid = interest if tranche == "Senior" else 0.0
            senior_principal_paid = principal if tranche == "Senior" else 0.0
            subordinate_interest_paid = interest if tranche == "Subordinate" else 0.0
            subordinate_principal_paid = principal if tranche == "Subordinate" else 0.0

            base_collections = (servicing_fee_paid + senior_interest_paid + senior_principal_paid
                                 + subordinate_interest_paid + subordinate_principal_paid)

            # CE draw trigger: rising 90+ DPD (Phase 2 requirement), read
            # straight from monthly_delinquency rather than the Phase 1
            # NPA-ratio proxy. "Rising" = this month's 90+ ratio is both
            # above a floor AND higher than last month's -- a pool that's
            # bad but STABLE doesn't draw CE here, only one deteriorating
            # further does. TO BE CONFIRMED: a real trigger would likely
            # be a contractual test (e.g. cumulative loss ratio vs a
            # trigger level in the deal's PTC agreement), not this proxy.
            dpd_row = dpd_lookup.get((deal_id, r["month"]))
            ratio_90plus = (float(dpd_row["pos_90plus_dpd"]) / pool_total) if (dpd_row and pool_total) else 0.0
            is_rising = (prev_90plus_ratio is not None
                         and ratio_90plus > prev_90plus_ratio
                         and ratio_90plus > 0.015)
            prev_90plus_ratio = ratio_90plus
            is_rough_month = is_rising and random.random() < 0.5

            if is_rough_month and ce_balance > 0:
                shortfall = round(base_collections * random.uniform(0.01, 0.05), 2)
                ce_drawn = round(min(shortfall, ce_balance), 2)
                excess_spread = 0.0
                collections_available = round(base_collections - shortfall + ce_drawn, 2)
            else:
                ce_drawn = 0.0
                excess_spread = round(pos * random.uniform(0.0, 0.005), 2)
                collections_available = round(base_collections + excess_spread, 2)

            ce_opening = round(ce_balance, 2)
            ce_balance = round(ce_balance - ce_drawn, 2)
            ce_closing = ce_balance

            # No linked senior POS for a Subordinate row -- left blank
            # rather than faked (assumption #8).
            ce_cover_ratio = round(ce_closing / pos, 4) if (tranche == "Senior" and pos > 0) else ""

            rows.append({
                "deal_id": deal_id, "month": r["month"],
                "collections_available": collections_available,
                "servicing_fee_paid": servicing_fee_paid,
                "senior_interest_paid": senior_interest_paid,
                "senior_principal_paid": senior_principal_paid,
                "subordinate_interest_paid": subordinate_interest_paid,
                "subordinate_principal_paid": subordinate_principal_paid,
                "excess_spread_to_originator": excess_spread,
                "ce_opening_balance": ce_opening,
                "ce_drawn_this_month": ce_drawn,
                "ce_closing_balance": ce_closing,
                "ce_cover_ratio": ce_cover_ratio,
            })
    return rows


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    master_rows, deal_rows = gen_master_code_and_deals()
    mis_rows = gen_monthly_mis(deal_rows)
    delinquency_rows = gen_monthly_delinquency(deal_rows, mis_rows)
    waterfall_rows = gen_monthly_waterfall(deal_rows, mis_rows, delinquency_rows)

    # strip internal helper fields before writing deals.csv
    deal_fieldnames = [k for k in deal_rows[0].keys() if not k.startswith("_")]
    for d in deal_rows:
        for k in list(d.keys()):
            if k.startswith("_"):
                del d[k]

    write_csv(os.path.join(OUTPUT_DIR, "master_code.csv"), master_rows,
               ["company", "instrument", "deal_name", "code", "status"])
    write_csv(os.path.join(OUTPUT_DIR, "deals.csv"), deal_rows, deal_fieldnames)
    write_csv(os.path.join(OUTPUT_DIR, "monthly_mis.csv"), mis_rows,
               ["deal_id", "month", "total_pool_outstanding", "closing_pos_investor_share",
                "total_bank_payout", "total_principal_payout", "scheduled_principal_payout",
                "npa_amount", "mclr", "roi", "investor_share_x_roi", "data_status"])
    write_csv(os.path.join(OUTPUT_DIR, "monthly_waterfall.csv"), waterfall_rows,
               ["deal_id", "month", "collections_available", "servicing_fee_paid",
                "senior_interest_paid", "senior_principal_paid", "subordinate_interest_paid",
                "subordinate_principal_paid", "excess_spread_to_originator",
                "ce_opening_balance", "ce_drawn_this_month", "ce_closing_balance",
                "ce_cover_ratio"])
    write_csv(os.path.join(OUTPUT_DIR, "monthly_delinquency.csv"), delinquency_rows,
               ["deal_id", "month", "pos_0dpd", "pos_1_30dpd", "pos_31_60dpd",
                "pos_61_90dpd", "pos_90plus_dpd", "cumulative_default_amount"])

    print(f"master_code.csv: {len(master_rows)} rows")
    print(f"deals.csv: {len(deal_rows)} rows")
    print(f"monthly_mis.csv: {len(mis_rows)} rows")
    print(f"monthly_waterfall.csv: {len(waterfall_rows)} rows")
    print(f"monthly_delinquency.csv: {len(delinquency_rows)} rows")
