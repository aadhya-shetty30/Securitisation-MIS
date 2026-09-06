"""
Phase 3 -- stress-testing engine.

Pure-Python, DB-agnostic: takes a plain dict describing a deal's CURRENT
state (already queried from Postgres by the caller, e.g. dashboard.py's
"Stress Testing" page) plus scenario shock parameters, and returns a
month-by-month projected DataFrame + a short summary dict. No database
or Streamlit imports here, so this is easy to reason about/test on its
own, separate from how the numbers get pulled out of Postgres.

============================================================
METHODOLOGY -- read before trusting any number out of this module
============================================================
This is explicitly a DEMO-GRADE projection: a straight-line
extrapolation of a deal's OWN trailing empirical roll rates and
write-off rate, not a refitted or loan-level model. Simplifications,
stated plainly rather than overclaimed precision:

1. DPD bucket roll-forward during the projection window assumes ZERO
   cures (see `project_dpd_buckets`) -- a deliberately conservative
   simplification. Real portfolios have some cure/recovery flow;
   omitting it makes this projection more pessimistic than a full model
   would be. Arguably the right conservative bias for a stress test, but
   not a claim that cures can't happen.

2. "Delinquency stress" (the task requirement) is implemented by
   multiplying BOTH the deal's trailing roll-forward rates
   (0->1-30->31-60->61-90->90+) AND its trailing write-off rate by the
   same stress factor. Rationale: a stress that worsens bucket migration
   but leaves eventual credit losses untouched wouldn't meaningfully
   stress CE, which is the whole point of this module.

3. Credit losses are assumed to hit CE at LGD = 100% -- every Rupee
   written off from the 90+ bucket needs full CE coverage, no partial
   recovery assumed. TO BE CONFIRMED / simplification: real
   loss-given-default on secured retail product is materially below
   100%; this is a conservative (worse-case) simplification, not a
   market fact.

4. Prepayment stress shocks the ANNUAL CPR by the given number of
   percentage points, then converts back to a monthly SMM via the exact
   inverse of the SMM->CPR formula in risk_metrics.py (not an
   approximation of it).

5. The 100%-pool balance backing the DPD buckets is assumed to run off
   at the SAME relative pace as the investor POS (pool_total(t) =
   pool_total(0) * POS(t)/POS(0)) -- avoids separately amortizing the
   100% pool figure, which this schema doesn't track a scheduled rate
   for independently of the investor share.

6. No CE replenishment (consistent with Phase 1/2) -- CE only ever
   draws down from its current balance.

7. Meaningful only for a Senior-tranche deal -- same limitation as
   Phase 1/2 (no linked senior POS for a Subordinate-tranche row in
   this schema, so no cover ratio to project).
"""

import pandas as pd

import risk_metrics


def shocked_monthly_smm(base_smm, prepayment_shock_pp):
    """Shift base_smm's ANNUALIZED CPR by prepayment_shock_pp percentage
    points (can be negative), then convert back to a monthly SMM -- an
    EXACT inversion of risk_metrics.annualize_smm's formula, not an
    approximation of it. Clipped to a [0%, 95%] CPR range as a sanity
    bound."""
    base_cpr = risk_metrics.annualize_smm(base_smm) or 0.0
    shocked_cpr = min(max(base_cpr + prepayment_shock_pp / 100.0, 0.0), 0.95)
    return 1 - (1 - shocked_cpr) ** (1 / 12)


def project_dpd_buckets(f0, f1, f2, f3, f4, roll_rates, write_off_rate,
                          stress_factor, horizon_months):
    """Project DPD bucket fractions (of the 100% pool) forward with ZERO
    assumed cures (assumption #1) and roll/write-off rates scaled by
    stress_factor (assumption #2, delinquency stress). Returns a list of
    dicts, one per projected month: bucket fractions plus that month's
    write-off fraction of the pool.

    Rates are capped at 100% after stressing: the balance-snapshot roll
    rate (sql/06_schema_delinquency.sql assumption #4) is a ratio of two
    noisy monthly balances and can occasionally read above 1.0 on a
    small or volatile pool, especially in the 90+ bucket where amounts
    are small -- a real transition can't move more than 100% of a
    bucket, so this is a sanity cap, not a modeling choice."""
    r01, r12, r23, r34 = (min(r * stress_factor, 1.0) for r in roll_rates)
    wo = min(write_off_rate * stress_factor, 1.0)

    out = []
    for m in range(1, horizon_months + 1):
        move_01 = f0 * r01
        move_12 = f1 * r12
        move_23 = f2 * r23
        move_34 = f3 * r34
        write_off = f4 * wo

        f0 = f0 - move_01
        f1 = f1 + move_01 - move_12
        f2 = f2 + move_12 - move_23
        f3 = f3 + move_23 - move_34
        f4 = f4 + move_34 - write_off

        f0, f1, f2, f3, f4 = (max(x, 0.0) for x in (f0, f1, f2, f3, f4))

        out.append({
            "month_index": m, "f0": f0, "f1": f1, "f2": f2, "f3": f3, "f4": f4,
            "write_off_fraction": write_off,
        })
    return out


def run_stress_scenario(deal_state, delinquency_stress_factor=1.0,
                          prepayment_shock_pp=0.0, horizon_months=24):
    """Run one scenario. deal_state is a dict with (all as of the deal's
    latest available month):
        beginning_pos    -- investor (Senior) POS, Rs Crs
        scheduled_rate   -- trailing monthly scheduled amortization rate (fraction)
        smm              -- trailing monthly SMM (fraction)
        ce_balance       -- current CE closing balance, Rs Crs
        pool_total       -- 100% pool outstanding, Rs Crs
        dpd_fractions    -- (f0, f1, f2, f3, f4) of pool_total
        roll_rates       -- (r01, r12, r23, r34) trailing empirical
        write_off_rate   -- trailing empirical monthly write-off rate
                             (fraction of the 90+ bucket)
        tranche          -- 'Senior' or 'Subordinate'

    Returns (projection_df, summary_dict). See module docstring for the
    full methodology and its simplifications.
    """
    smm_shocked = shocked_monthly_smm(deal_state["smm"], prepayment_shock_pp)

    schedule = risk_metrics.project_paydown_schedule(
        deal_state["beginning_pos"], deal_state["scheduled_rate"], smm_shocked,
        max_months=horizon_months,
    )
    dpd_path = project_dpd_buckets(
        *deal_state["dpd_fractions"], roll_rates=deal_state["roll_rates"],
        write_off_rate=deal_state["write_off_rate"],
        stress_factor=delinquency_stress_factor, horizon_months=horizon_months,
    )

    rows = []
    ce_balance = deal_state["ce_balance"]
    pos0 = deal_state["beginning_pos"] or 0.0
    pool0 = deal_state["pool_total"] or 0.0
    ce_depleted_month = None
    payout_impacted_month = None

    for m in range(1, horizon_months + 1):
        sched_row = schedule[m - 1] if m <= len(schedule) else None
        dpd_row = dpd_path[m - 1]

        # Once the projected paydown schedule ends (balance hit ~0), the
        # deal has fully repaid -- hold POS at 0 for any remaining months.
        pos_t = sched_row["ending_balance"] if sched_row else 0.0
        pool_t = pool0 * (pos_t / pos0) if pos0 else 0.0

        write_off_amount = dpd_row["write_off_fraction"] * pool_t
        ce_draw = min(write_off_amount, ce_balance)
        shortfall = write_off_amount - ce_draw  # unfunded loss this month

        ce_balance = round(ce_balance - ce_draw, 4)
        if ce_balance <= 0.001 and ce_depleted_month is None:
            ce_depleted_month = m
        if shortfall > 0.001 and payout_impacted_month is None and deal_state["tranche"] == "Senior":
            payout_impacted_month = m

        cover_ratio = (ce_balance / pos_t) if pos_t > 0 else None

        rows.append({
            "month_index": m,
            "pos": pos_t,
            "pool_total": pool_t,
            "f4_90plus": dpd_row["f4"],
            "write_off_amount": write_off_amount,
            "ce_draw": ce_draw,
            "ce_balance": ce_balance,
            "ce_cover_ratio": cover_ratio,
            "senior_payout_shortfall": shortfall,
        })

    df = pd.DataFrame(rows)
    wal_years = risk_metrics.wal_years_from_schedule(schedule, pos0)

    summary = {
        "wal_years": wal_years,
        "ce_depleted_month": ce_depleted_month,
        "payout_impacted_month": payout_impacted_month,
        "final_ce_cover_ratio": df["ce_cover_ratio"].iloc[-1] if not df.empty else None,
    }
    return df, summary
