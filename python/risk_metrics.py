"""
Shared credit-risk math used by both the "Credit Performance" dashboard
page (Phase 2) and the stress-testing engine (Phase 3), so the two don't
each invent their own CPR/WAL logic.

No database access here -- pure functions over pandas DataFrames /
plain numbers, so they're easy to unit-test and to reuse from a stress
scenario that isn't reading from calc.deal_smm_cpr at all.

------------------------------------------------------------
TO BE CONFIRMED / documented simplifications
------------------------------------------------------------
1. WAL is a CLOSED-FORM approximation, not a projected cash-flow
   schedule. Given a constant assumed monthly paydown rate r (scheduled
   amortization + prepayment SMM), a pool run off at that constant rate
   forever has balance_t = balance_0 * (1-r)^t and monthly principal_t =
   balance_(t-1) * r. Weighted average life in months is then:
       WAL_months = sum_{t=1..inf} t * principal_t / balance_0
                  = r * sum_{t=1..inf} t * (1-r)^(t-1)
                  = r * 1/r^2 = 1/r
   i.e. WAL_years = 1 / (12 * r). This is the standard simplification for
   a constant-CPR assumption -- adequate for a demo trend, NOT a
   precision figure. A production WAL would run an explicit month-by-
   month projected cash flow and sum t * principal_t / balance_0 directly
   (see `project_paydown_schedule` below, used by the Phase 3 stress
   engine, which *does* do this explicitly rather than using the
   closed form -- the closed form here is for a quick per-month trend
   metric, the explicit projection is for scenario comparisons where
   showing the actual month-by-month path matters).
2. SMM/CPR: SMM = max(actual principal - scheduled principal, 0) /
   (beginning POS - scheduled principal); CPR = 1 - (1-SMM)^12. Standard
   MBS/ABS convention. See sql/06_schema_delinquency.sql assumption #5
   for what "scheduled" means in this synthetic dataset.
"""

import pandas as pd


def annualize_smm(smm):
    """CPR from a single month's SMM. Returns None if smm is None/NaN."""
    if smm is None or pd.isna(smm):
        return None
    return 1 - (1 - smm) ** 12


def wal_years_from_rate(monthly_paydown_rate):
    """Closed-form WAL (years) assuming a constant monthly paydown rate
    forever -- see module docstring assumption #1. Returns None if the
    rate is None/NaN/<=0 (a non-positive rate implies the pool never
    pays down under this simplification)."""
    if monthly_paydown_rate is None or pd.isna(monthly_paydown_rate) or monthly_paydown_rate <= 0:
        return None
    return 1.0 / (12.0 * monthly_paydown_rate)


def add_rolling_cpr_wal(df, window=3):
    """Given a per-deal-per-month DataFrame with columns
    [deal_id, month, scheduled_principal_payout, beginning_pos, smm],
    add trailing rolling-average columns:
        scheduled_rate          -- scheduled_principal_payout / beginning_pos
        smm_trailing            -- rolling mean of smm over `window` months
        scheduled_rate_trailing -- rolling mean of scheduled_rate
        cpr_trailing            -- annualized from smm_trailing
        wal_years               -- closed-form WAL from
                                    (scheduled_rate_trailing + smm_trailing)

    This is where the "recomputed monthly as prepayment experience
    updates" requirement is implemented: each month gets its own trailing
    window, so WAL/CPR shift as new months of (simulated) prepayment
    experience arrive. Rolling (not just latest) smooths out the
    month-to-month noise in a single SMM observation before it feeds the
    WAL closed form.
    """
    df = df.sort_values(["deal_id", "month"]).copy()
    df["scheduled_rate"] = df["scheduled_principal_payout"] / df["beginning_pos"]
    df.loc[~pd.notna(df["beginning_pos"]) | (df["beginning_pos"] <= 0), "scheduled_rate"] = None

    grouped = df.groupby("deal_id")
    df["smm_trailing"] = grouped["smm"].transform(lambda s: s.rolling(window, min_periods=1).mean())
    df["scheduled_rate_trailing"] = grouped["scheduled_rate"].transform(
        lambda s: s.rolling(window, min_periods=1).mean()
    )
    df["cpr_trailing"] = df["smm_trailing"].apply(annualize_smm)
    df["combined_monthly_rate"] = df["scheduled_rate_trailing"].fillna(0) + df["smm_trailing"].fillna(0)
    df["wal_years"] = df["combined_monthly_rate"].apply(wal_years_from_rate)
    return df


def project_paydown_schedule(beginning_balance, monthly_scheduled_rate, monthly_smm,
                               max_months=360):
    """Explicit month-by-month projection of a pool running off at a
    CONSTANT scheduled amortization rate and CONSTANT SMM (straight-line
    extrapolation of current experience -- see Phase 3's stress-testing
    methodology note). Returns a list of dicts:
        [{month_index, beginning_balance, scheduled_principal,
          prepayment, total_principal, ending_balance}, ...]
    stopping once the balance is effectively zero (< 0.01) or
    max_months is reached (safety cap -- this is a demo projection, not
    a production amortization engine).

    Used by the Phase 3 stress engine so a stress scenario's projected
    CE cover ratio / WAL path can be built from the same paydown logic
    as the base case, just with shocked rate inputs.
    """
    schedule = []
    balance = beginning_balance
    combined_rate = (monthly_scheduled_rate or 0) + (monthly_smm or 0)
    for month_index in range(1, max_months + 1):
        if balance < 0.01:
            break
        scheduled = balance * (monthly_scheduled_rate or 0)
        prepay = balance * (monthly_smm or 0)
        total_principal = min(scheduled + prepay, balance)
        ending = max(balance - total_principal, 0)
        schedule.append({
            "month_index": month_index,
            "beginning_balance": balance,
            "scheduled_principal": scheduled,
            "prepayment": prepay,
            "total_principal": total_principal,
            "ending_balance": ending,
        })
        balance = ending
        if combined_rate <= 0:
            # Rate is zero -- balance will never amortize; stop rather
            # than spin through max_months doing nothing.
            break
    return schedule


def wal_years_from_schedule(schedule, beginning_balance):
    """WAL (years) computed directly from an explicit projected schedule
    (sum of t * principal_t / balance_0), rather than the closed form.
    Used by the Phase 3 stress engine for before/after comparisons."""
    if not schedule or beginning_balance <= 0:
        return None
    weighted_months = sum(row["month_index"] * row["total_principal"] for row in schedule)
    return (weighted_months / beginning_balance) / 12.0
