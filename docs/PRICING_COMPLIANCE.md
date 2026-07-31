# Pricing compliance — what the cost volume must satisfy

Research date: **2026-07-31**. Owner: pricing.
Code: `bidpilot/pricing/compliance.py`, `bidpilot/pricing/rates.py`,
`bidpilot/data/clause_patterns.py`.

This is the regulatory layer under the estimate. It exists because a
technically excellent proposal with a non-compliant cost volume is not a
close call — it is unawardable, and in a few cases it is a breach of a
warranty the offeror signed.

## Evidence quality — read this before citing anything here

The research behind this document was gathered on 2026-07-31 by search
against primary sources (acquisition.gov, eCFR, DOL). **Direct fetch of those
pages was blocked at the network proxy**, so every statement below is
extraction from search results over primary pages, cross-checked across
independent hits — not a read of the primary text itself. Rules of
construction (how a clause operates) held consistently across sources and are
reliable. **Dollar thresholds did not**, and are marked accordingly.

| Label | Meaning |
|---|---|
| *sourced* | Consistent across independent search results over primary pages |
| *[unverified]* | Found, but with conflicting or unconfirmable attribution |
| *inference* | Our reading of how the rule applies to this codebase |

## Two rules of construction

**1. The solicitation governs. Never infer a clause from dollar value.**

The Revolutionary FAR Overhaul (EO 14275) has agencies deleting and
renumbering FAR Part 15 and Part 22 provisions through class deviations
(*sourced*). A $5M services RFP may legitimately carry no professional-
compensation provision. So `compliance.py` decides applicability from the
literal presence of a clause in the solicitation corpus, and uses thresholds
only to *explain an absence*, never to manufacture a requirement.

**2. Thresholds are dated advisories, and never hard-fail.**

The certified cost or pricing data threshold moved $2M → $2.5M → $10M inside
about twelve months. The codified FAR text lags the statute. Every constant in
`compliance.py` carries its cite and `AS_OF`, and every threshold-derived
finding is `info`.

## The clauses, and what each one obliges

### FAR 52.222-43 / -44 — SCA price adjustment

**This is the one that changes the arithmetic.** Paragraph (b) of 52.222-43 is
an express **warranty** that the contract price contains no allowance for a
contingency covering increased SCA wages and fringe (*sourced*). It is not a
prohibition you might argue around — you warranted it.

The clause is itself the escalation mechanism: when a new wage determination
is incorporated at an option exercise, the contractor claims the delta.
Consequences that shape the estimate (*sourced*):

- The adjustment is **bilateral** — it is not automatic, it must be claimed.
- It covers the wage/fringe delta plus only the **accompanying increases in
  social security and unemployment taxes and workers' compensation** — **no
  overhead, no G&A, and no profit** on the increase.
- There is a **30-day window** to claim after the new WD is incorporated.
- 52.222-44 (no-option contracts) is also triggered by a **statutory FLSA
  amendment**, not only by a new WD.

**What the code does.** `price_estimate(..., sca_price_adjustment=True)` holds
every WD-covered labor category **flat** across option years and escalates only
uncovered (professional/exempt) categories. The flag is set from the clause
scan, not from a guess. Escalating a covered category is the single hard
finding in this module (`FAR 52.222-43(b)`), and it blocks export.

**What it means for your margin** (*inference*): because the adjustment
carries no OH/G&A/profit, your indirect recovery on SCA labor erodes over a
five-year period of performance. That is the intended design of the clause, not
a modeling error — plan for it in fee, not in the base rate.

### FAR 52.222-46 — compensation of professional employees

Present → a Total Compensation Plan is required, and paragraph (d) permits
**rejecting the proposal** for an inadequate one (*sourced*). The plan needs
salary ranges by category and itemized fringe, supported by dated **regional**
survey data — BLS OES plus a recognized commercial survey. On a successor
procurement, every category priced below the incumbent needs a written
justification against the paragraph (b) criteria: program continuity,
uninterrupted high-quality work, availability of qualified people.

Absent → the module emits an `info` finding above ~$900,000 explaining that the
absence is *expected* under RFO deviations, and that no plan is required. The
$900,000 prescription threshold is **[unverified]** — search returned it with
conflicting FAC attribution — and is used only to explain the absence.

SCA and professional-employee coverage are **mutually exclusive** for a given
employee (29 CFR 4.156) (*sourced*): a bona fide professional is exempt from
the SCA. A category cannot be simultaneously WD-floored and covered by
52.222-46.

### FAR 52.237-10 — uncompensated overtime

Applies to hours-based professional/technical services over the simplified
acquisition threshold (*sourced*). The proposal must state whether UCOT is
proposed — "we didn't mention it" is not an answer.

If UCOT is proposed: disclose UCOT hours and rates by category at the same
level of detail as compensated hours, and apply the **adjusted hourly rate**

    adjusted rate = rate x 40 / proposed hours per week

to **all** proposed hours, not just the hours above 40 (*sourced*). Getting
this backwards understates cost and is a standard evaluator catch.

### FAR 52.215-20 / -21 and certified cost or pricing data

The current threshold is **$10,000,000** for awards after 2026-06-30 (FY2026
NDAA §1804), up from $2,500,000 (*sourced*, but see the threshold caveat —
the codified FAR may still read the older figure). Exceptions under FAR
15.403-1(b): adequate price competition, commercial product/service, prices
set by law or regulation, HCA waiver.

If it applies, expect a FAR 15.408 Table 15-2 submission (renumbered to
15.408-2 Table 15-1 in RFO drafts — the scanner matches both): time-phased
labor by category, indirect pool and base build-ups, ODC bases, and an
affirmative statement if facilities capital cost of money is not proposed.

The Certificate of Current Cost or Pricing Data is executed **at price
agreement, not at submission** (*sourced*). Nothing in this system pre-answers
it (invariant 1).

**FCCM: propose it or forfeit it.** Under FAR 52.215-17, facilities capital
cost of money not proposed is permanently unallowable on that contract
(*sourced*). The clause scanner detects it; the estimator does not compute
FCCM, so this remains a human line item.

### FAR 31.205 — expressly unallowable costs

`UNALLOWABLE_PATTERNS` screens ODC and indirect descriptions for the ten
categories most likely to appear in a small services proposal: entertainment
(-14), alcohol (-51, no exception at all), lobbying (-22), donations (-8),
fines and penalties (-15), interest on borrowings (-20), bad debts (-3),
goodwill (-49), airfare above lowest available (-46(b)), and non-recruitment
advertising (-1).

Every hit is `soft` and names its cite — this is a screen worth its false
positives, because FAR 42.709 imposes a penalty equal to the disallowed
amount, **doubled** if the cost was previously determined unallowable
(*sourced*).

### CAS — and the small business exemption

Full CAS coverage attaches above **$35,000,000** (FY2026 NDAA §1806)
(*sourced*, threshold caveat applies). But **small business concerns are
exempt from all Cost Accounting Standards at any contract value** (48 CFR
9903.201-1(b)(3)) (*sourced*). The module says this out loud, because the
headline threshold reads as though it applies to everyone and it does not.

### Cost-reimbursement work — SF 1408

FAR 16.301-3(a)(1) requires an accounting system determined adequate before a
cost-reimbursement award (*sourced*). The finding lists the SF 1408 criteria
the preaward survey tests: segregate direct from indirect, accumulate direct
costs by contract, allocate indirect on a consistent logical base, general-
ledger control, labor identified to cost objectives via timekeeping and labor
distribution, costs determined at least monthly, FAR Part 31 unallowables
excluded.

## What changed in 2025–2026 (and what did not)

*sourced*:

- **EO 14026** ($17.75/hr federal contractor minimum wage) has been **revoked**.
  Do not price to it.
- **EO 14055** (nondisplacement of qualified workers) has been **revoked**.
- **CBA successorship survives both.** Where a predecessor collective
  bargaining agreement applies, its rates are a self-executing floor under the
  SCA independent of any executive order.

## Severity model

| Severity | Effect | When |
|---|---|---|
| `hard` | Blocks export via `qa_checks.pricing_check` | Unambiguous legal violation computable from the numbers |
| `soft` | Reviewer to-do in `HUMAN_ACTIONS.md` | A required submission is missing, or a cost looks unallowable |
| `info` | Context in the memo | Threshold-derived, or an explained absence |

Only two things are `hard`: paying an SCA-covered category below its wage
determination floor (`rates.py`, pre-existing), and escalating an SCA-covered
category under a 52.222-43 warranty (`compliance.py`). Everything else is
advisory, because everything else depends on facts the system cannot verify.

## Output

Every run writes `pricing/REGULATORY_COMPLIANCE.md` alongside
`basis_of_estimate.md` and `priced_lines.csv`. Hard findings also appear in
`HUMAN_ACTIONS.md` with a 🛑 marker, and each finding's remedy becomes a
pricing action item.

## What this is not

A compliance screen, not legal advice, and not a substitute for contracts
review. It reports what the numbers and the clause text say. Re-confirm every
threshold against the solicitation and eCFR before relying on one externally.

## Maintenance

Thresholds rot. When `AS_OF` is more than a year old, re-verify
`TINA_THRESHOLD_USD`, `SIMPLIFIED_ACQUISITION_THRESHOLD_USD`,
`MICRO_PURCHASE_THRESHOLD_USD`, `PROFESSIONAL_COMP_THRESHOLD_USD`, and
`CAS_MANDATORY_THRESHOLD_USD` against eCFR, and update `AS_OF`. The rules of
construction change far more slowly than the numbers do.
