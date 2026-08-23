"""
Step 5 -- Synthetic data generator for the NBFC PTC/DA securitisation MIS project.

Produces three CSVs matching the raw schema in sql/01_schema_raw.sql:
    master_code.csv
    deals.csv
    monthly_mis.csv

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
"""

import csv
import random
from datetime import date
from dateutil.relativedelta import relativedelta

random.seed(42)  # reproducible dataset

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
            "processing_fees_paid": round(amount_securitised * 0.001, 2),
            "documentation_charges": round(random.uniform(0.5, 3), 2),
            "external_ca_fees": round(random.uniform(1, 5), 2),
            "legal_documentation_fees": round(random.uniform(1, 4), 2),
            "rating_fees": round(random.uniform(2, 8), 2),
            "trustee_fees_pa": round(random.uniform(0.5, 2), 2),
            "arranger_fees": round(amount_securitised * 0.0025, 2),
            "_borrowing_date_obj": borrowing_date,  # internal use for monthly_mis gen
            "_amount_securitised": amount_securitised,
            "_deal_roi": deal_roi,
            "_status": status,
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
        monthly_amort_rate = random.uniform(0.02, 0.05)  # 2-5% runoff per month

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

            pos = max(pos * (1 - monthly_amort_rate), 0)
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
                    "npa_amount": "", "mclr": "", "roi": "", "investor_share_x_roi": "",
                    "data_status": data_status,
                })
                continue

            payout = round(pos * (monthly_roi / 100) / 12, 2)
            principal_payout = round(amount * monthly_amort_rate, 2)

            rows.append({
                "deal_id": d["deal_id"], "month": m.isoformat(),
                "total_pool_outstanding": round(pos * random.uniform(1.0, 1.15), 2),
                "closing_pos_investor_share": round(pos, 2),
                "total_bank_payout": payout,
                "total_principal_payout": principal_payout,
                "npa_amount": npa_amount,
                "mclr": monthly_mclr, "roi": monthly_roi,
                "investor_share_x_roi": round(pos * monthly_roi / 100, 2),
                "data_status": data_status,
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

    # strip internal helper fields before writing deals.csv
    deal_fieldnames = [k for k in deal_rows[0].keys() if not k.startswith("_")]
    for d in deal_rows:
        for k in list(d.keys()):
            if k.startswith("_"):
                del d[k]

    write_csv("/home/claude/nbfc-mis/python/master_code.csv", master_rows,
               ["company", "instrument", "deal_name", "code", "status"])
    write_csv("/home/claude/nbfc-mis/python/deals.csv", deal_rows, deal_fieldnames)
    write_csv("/home/claude/nbfc-mis/python/monthly_mis.csv", mis_rows,
               ["deal_id", "month", "total_pool_outstanding", "closing_pos_investor_share",
                "total_bank_payout", "total_principal_payout", "npa_amount", "mclr", "roi",
                "investor_share_x_roi", "data_status"])

    print(f"master_code.csv: {len(master_rows)} rows")
    print(f"deals.csv: {len(deal_rows)} rows")
    print(f"monthly_mis.csv: {len(mis_rows)} rows")
