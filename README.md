# Securitisation-MIS

Automated NBFC MIS & Management Dashboard using PostgreSQL, Python and Streamlit — transforming deal and monthly MIS data into validated KPIs, payout analytics, credit-risk analytics, and interactive management reporting.

Originally a redesign of a manual Excel-based securitisation MIS workbook (raw input tabs, VLOOKUP/CHOOSE formula sheets, and presentation sheets, all tangled together) into a proper layered system: raw tables → SQL calculation views → an interactive dashboard. It has since been extended well past that original scope into a small credit-risk / treasury analytics project — waterfall & credit enhancement modeling, delinquency/prepayment analytics, a stress-testing engine, a funding-cost/ALM lens, and basic regulatory markers — built to demonstrate securitisation and structured-finance concepts, not to replicate a production risk system.

**⚠️ Data disclaimer.** Every number in this repository — deal amounts, ROIs, dates, DPD buckets, cash flows, everything — is synthetically generated (`python/generate_synthetic_data.py`, seeded and reproducible). No real IIFL deal, borrower, or financial data is used anywhere. One deliberate exception to flag explicitly: the synthetic `company` field on each fabricated deal is labeled `"IIFL Finance Limited"` / `"IIFL Samasta Finance"` — real, named legal entities — even though every number attached to that label is fabricated. This was a deliberate choice (kept for internal/interview use with this caveat stated up front), not an oversight. **Before this repo, dashboard, or any screenshot of it is shared publicly or with anyone outside this context, that choice should be revisited** — genericizing the company label (e.g. to a placeholder originator name) is a one-line change in `generate_synthetic_data.py`.

---

## Architecture

```
sql/
  01_schema_raw.sql          raw.* tables -- the ONLY manually/synthetically populated tables
  02_schema_calc.sql         calc.* views -- per-deal calculated fields, rate projection, data-quality flags
  03_schema_aggregates.sql   calc.* views -- investor/executive/quarterly summaries (dashboard-facing)
  04_schema_auth.sql         auth.* -- RBAC (viewer/uploader/admin) + audit log
  05_schema_waterfall.sql    tranche/CE columns on raw.deals, raw.monthly_waterfall, CE views
  06_schema_delinquency.sql  DPD/prepayment columns, raw.monthly_delinquency, roll-rate/vintage/CPR views
  07_schema_funding.sql      stated tenor + calc.deal_funding_cost (all-in cost of funds)
  08_schema_regulatory.sql   MHP/true-sale columns, MRR/MHP/true-sale compliance views

python/
  generate_synthetic_data.py   produces all 5 CSVs (master_code, deals, monthly_mis, monthly_waterfall, monthly_delinquency)
  load_to_postgres.py          schema-first, then data: runs 7 of the 8 SQL files (all except
                                04_schema_auth.sql, applied separately), then COPYs the 5 CSVs
  seed_admin.py                creates/updates a dashboard login (viewer/uploader/admin role)
  risk_metrics.py              shared CPR/SMM/WAL math -- no DB or Streamlit imports, reused by dashboard.py and stress_engine.py
  stress_engine.py             the Phase 3 stress-testing engine -- pure Python, DB-agnostic
  dashboard.py                 the Streamlit app -- ZERO business logic, every number comes from a query
```

**Design principle carried through every phase**: the dashboard should contain no business logic. Anything expressible as a straight SQL aggregation lives in a `calc.*` view; anything that genuinely needs iteration (WAL, CPR trailing averages, the stress projection) lives in `risk_metrics.py` / `stress_engine.py`, called by `dashboard.py` but usable independently of it. If a number looks wrong, the fix belongs in SQL or in one of those two modules — never in `dashboard.py` itself.

### Running it

```bash
cd python
python generate_synthetic_data.py          # writes 5 CSVs into python/
export NBFC_MIS_DB_URL="postgresql://...neon-connection-string..."
python load_to_postgres.py                  # builds schema, loads data (needs a FRESH database --
                                             # 01_schema_raw.sql uses CREATE TABLE, not CREATE TABLE IF NOT EXISTS)
python seed_admin.py                        # create your first admin login
streamlit run dashboard.py
```

`04_schema_auth.sql` (RBAC/audit) is independent of the raw/calc data layer and isn't run by `load_to_postgres.py` — apply it once yourself alongside the other 7 files on a fresh database.

Regenerating `generate_synthetic_data.py` after a code change **shifts the random sequence for every subsequent draw** (it's `random.seed(42)`-reproducible for a *given* version of the script, not stable across edits to it). Each phase in this project's build history changed the generator, so re-running it means a full reload against a clean database, not an incremental patch — this happened repeatedly during development and is expected, not a bug.

---

## Dashboard pages

| Page | What it shows | RBAC |
|---|---|---|
| Management Summary | Investor-wise KPIs, product/instrument mix | Everyone |
| Executive Review | FY/QoQ performance, auto-generated narrative | Everyone |
| Data Quality | Missing/Incomplete/Available breakdown | Everyone |
| Waterfall & Credit Enhancement | Monthly payment waterfall, CE cover ratio trend | Everyone |
| Credit Performance | DPD trends, roll-rate heatmap, vintage curves, CPR/WAL | Everyone |
| Stress Testing | Delinquency/prepayment shock scenarios vs base case | Everyone |
| Funding & ALM | All-in cost vs illustrative alternatives, WAL-vs-tenor check | Everyone |
| Regulatory Compliance | MRR/MHP/true-sale flags, combined summary table | Everyone |
| Upload monthly MIS | CSV upload into `raw.monthly_mis` | Uploader, Admin |
| User management | Create/deactivate users, view audit log | Admin |

---

## Methodology, in plain English

Every SQL file above opens with a numbered assumptions block — this section is a plain-English tour of the same ground, not a replacement for it. Where something is a genuine simplification (not a market fact), it's called out as a **Simplifying assumption**.

### Deal structure (Phase 1)

A PTC (Pass-Through Certificate) deal in this schema is one **tranche** — Senior or Subordinate — with one dominant **credit enhancement** mechanism (Cash Collateral, Overcollateralization, or a First Loss Guarantee). A DA (Direct Assignment) deal has no tranche and no CE, because DA is a proportional co-ownership transfer of receivables, not a tranched issuance with a payment waterfall.

> **Simplifying assumption**: a real PTC often issues a Senior *and* a Subordinate tranche against the *same* pool, linked together. This schema has no `pool_id` connecting a paired tranche pair — each is generated as an independent deal row. The practical consequence: a Subordinate-tranche deal can't look up "its" Senior tranche's outstanding balance, so its CE cover ratio is left blank rather than faked.

### The monthly waterfall (Phase 1)

Each month, a PTC deal's collections are applied in priority order: **servicing fee → senior interest → senior principal → subordinate interest → subordinate principal → excess spread to the originator.** This sequential-pay order is standard market convention for an Indian PTC structure.

> **Simplifying assumption**: that priority order has not been confirmed against any specific deal's actual Payment Waterfall clause — it's the textbook default, not a verified fact about a real transaction.

**Credit enhancement (CE)** is a reserve — cash collateral, overcollateralization, or a guarantee — sized at deal inception (`credit_enhancement_initial_amount`) that absorbs pool losses so senior investors keep getting paid on schedule even if the pool underperforms. **CE cover ratio** = CE closing balance ÷ outstanding senior POS; a ratio below 1.0x means the reserve no longer fully covers the senior balance, which is exactly the kind of trigger a real deal's transaction documents would define more precisely.

> **Simplifying assumption**: CE only ever draws down in this model — it never replenishes from trapped excess spread, even though a real overcollateralization structure can rebuild that way.

### Delinquency, default, and prepayment (Phase 2)

Every deal-month is split into DPD (Days Past Due) buckets — current, 1-30, 31-60, 61-90, 90+ — plus a running `cumulative_default_amount`. Buckets are generated with a simple per-deal "credit trajectory" (Clean / Deteriorating / Recovering) driving month-over-month bucket migration, so a deal's delinquency actually trends instead of jittering randomly.

**Roll rate** (e.g. "what % of last month's 31-60 DPD balance became 61-90 DPD this month") is computed the way most MIS systems do it when only bucket *balances* are available, not loan-level traces: `this month's higher bucket ÷ last month's adjacent lower bucket`. It's a standard approximation, not a precise flow measurement — a bucket can be inflated or deflated in the same month by cures and new formations netting against each other.

**CPR (Conditional Prepayment Rate)** is built the standard MBS/ABS way:
```
SMM (Single Monthly Mortality) = max(actual principal − scheduled principal, 0) / (beginning POS − scheduled principal)
CPR (annualized)                = 1 − (1 − SMM)^12
```
> **Simplifying assumption**: "scheduled principal" here is this generator's own flat contractual amortization rate applied to the declining balance — a real pool's scheduled amortization would follow the underlying loans' actual EMI schedules, which can vary month to month. What counts as "prepayment" vs "scheduled" should be re-derived from a real data source before this formula is trusted beyond a demo.

**WAL (Weighted Average Life)** uses a closed-form shortcut, not a full projected cash-flow schedule: if a pool ran off forever at a constant monthly rate `r` (scheduled amortization + trailing prepayment), its weighted average life works out to `WAL (years) = 1 / (12 × r)`. It's the standard simplification for a constant-CPR assumption, adequate for a trend line, not a precision figure — recomputed every month as the trailing rate updates.

### Stress testing (Phase 3)

The stress engine (`python/stress_engine.py`) takes a Senior-tranche deal's *current, empirically observed* state — its trailing roll rates, write-off rate, SMM, CE balance — and projects it forward under a shock:

- **Delinquency stress** (1.5x / 2x / 3x) multiplies the deal's own trailing roll rates *and* its write-off rate by that factor.
- **Prepayment stress** shifts the annualized CPR by a chosen number of percentage points, converted back to a monthly rate via the exact inverse of the CPR formula above.

It then tracks, month by month: the CE cover ratio, whether CE fully depletes (and when), whether senior payout gets impacted, and the resulting WAL shift.

> **Simplifying assumptions, stated plainly rather than overclaimed**: the projection assumes **zero cures** during the stress horizon (deliberately conservative — real portfolios do have some recovery flow); it assumes **100% loss-given-default** on written-off principal (real LGD on secured retail product is materially lower — again conservative, not a market fact); and the 100%-pool balance backing the DPD buckets is assumed to run off at the *same relative pace* as the investor POS, rather than being separately amortized. This is a demo-grade straight-line extrapolation of a deal's own history, explicitly not a production risk model — that distinction is worth stating plainly in an interview rather than overclaiming precision.

### Funding cost & ALM (Phase 4)

**All-in cost of funds** = investor payout rate + servicing fee % + one-time execution costs (processing, legal, rating, trustee, arranger fees) amortized straight-line over the deal's *stated* tenor. It's compared against illustrative — **not live, not real IIFL** — reference rates for NCD, term loan, and commercial paper funding, clearly labeled as typical-market placeholders.

> **Simplifying assumption**: amortizing execution costs over the *stated* tenor (rather than the pool's actual expected life, i.e. WAL) understates the true effective cost, since WAL is typically shorter than stated tenor once prepayment is considered. The dashboard shows the WAL-vs-tenor comparison alongside this figure for exactly that reason.

**WAL-vs-tenor ALM check**: flags a deal where its pool's actual weighted average life diverges meaningfully from the PTC's stated tenor at issuance — a simple asset-liability mismatch indicator.

> **Known dataset limitation, disclosed rather than hidden**: this generator's scheduled amortization rate isn't tied to a deal's product or stated tenor, so longer-tenor products (HCF, LAP) show up as *systematically* "mismatched" here, rather than the check surfacing occasional genuine outliers. That's a gap in the synthetic data's realism, not a flaw in the check's logic — a real generator would tie amortization pace to stated tenor first, so a flagged mismatch would actually mean something. (See "How I'd extend this" below.)

### Regulatory markers (Phase 5)

**MRR (Minimum Retention Requirement)** — the % of the pool the originator must retain — is checked against a threshold that's adjustable directly on the dashboard (RBI's real thresholds are tiered by loan tenor, which isn't tracked precisely enough here to hardcode).

**MHP (Minimum Holding Period)** — how much repayment history a loan needed before it could be securitized — is approximated here as ≤24 months stated tenor → 3 months required, otherwise → 6 months.

> **Simplifying assumption**: RBI's actual MHP matrix depends on both loan tenor *and* repayment frequency, neither of which this schema tracks precisely enough to reproduce verbatim. This is an illustrative simplification of the real matrix, not a substitute for checking the current RBI Master Direction on Securitisation of Standard Assets.

**True-sale assumptions** are recorded per deal as a boolean + a free-text note — explicitly **an assumption this project makes, not a legal determination**. A real true-sale opinion requires actual review of the transfer documents by counsel, which is out of scope for a synthetic project.

All three are surfaced as a simple compliance summary table — deliberately not a rules engine, per the project's own scope.

---

## How I'd extend this with more time

- **Loan-level roll-rate and vintage models** instead of the aggregate, bucket-balance Markov approximation used here — real roll rates and cure rates need loan-level payment history, not just monthly bucket snapshots.
- **Monte Carlo stress paths** instead of a single deterministic straight-line projection per scenario — would give a distribution of outcomes (e.g. P10/P50/P90 CE depletion month) rather than one point estimate per stress factor.
- **A shared `pool_id`** linking a paired Senior/Subordinate tranche back to the same underlying pool, so a Subordinate tranche's CE cover ratio could reference its actual paired Senior balance instead of being left blank.
- **CE replenishment mechanics** (trapped excess spread rebuilding an overcollateralization reserve), which this model deliberately omits.
- **Tie the synthetic amortization rate to stated tenor/product** at generation time, so the WAL-vs-tenor ALM check would surface genuine, occasional mismatches instead of a systematic pattern driven by a generator gap.
- **Real RBI MRR/MHP thresholds**, tenor- and repayment-frequency-aware, replacing the simplified flat-threshold approximations used here.
- **Real market rate feeds** for the funding-cost comparison (NCD/term loan/CP), replacing the illustrative placeholder rates.
- **SSO/MFA** for the auth layer — `04_schema_auth.sql` already documents this as a deliberately-scoped-out, realistic gap for a lightweight internal tool rather than something faked.
- **Materialized views + a refresh job** if the dataset grew large enough that the current recompute-on-query `calc.*` views became too slow (flagged as a scaling note in `02_schema_calc.sql` from the very first version of this project).

This list is meant to show where the line between "demo" and "production system" actually sits — being able to name the gap honestly is the point, not a weakness to hide.
