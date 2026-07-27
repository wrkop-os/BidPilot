# Market research dossier — kb.pro

Research date: **2026-07-27**. Four parallel research streams (labor rates,
indirect-rate norms, SCA compliance floors, estimating/bid benchmarks)
feeding every number in this KB. Numbers are **[verified]** (specific source
surfaced the figure), **[derived]** (computed from verified inputs, method
shown), or **[flagged]** (practitioner convention / unverifiable — never
cite these as fact in a proposal).

Meridian Federal Systems LLC is a fictional company; its *rates and
benchmarks* are market-grounded per below, while its identity, contracts,
and personnel are illustrative.

---

## 1. Direct labor rates (basis for profile.yaml `labor_categories`)

Basis: BLS OEWS May 2024 national medians (annual / 2,080), uplifted 15-20%
for the DC-metro market. The BLS all-occupation DC-metro premium is +33%
($43.47 vs $32.66 mean) [verified]; IT-specific premiums typically run
+10-25% [flagged — DC per-SOC rows unreachable].

| Category | SOC anchor | BLS national median | Meridian direct | Burdened @2.11 wrap | Observed GSA market |
|---|---|---|---|---|---|
| Program Manager | 11-3021 | $82.31 | $95.00 | ~$201 | CALC avg ~$166; awards $150–$265 [verified] |
| Project Manager | 13-1082 | $48.44 | $62.00 | ~$131 | MAS band $120–$185 [verified] |
| Sr Systems Engineer | 15-1241 proxy | $62.69 | $80.00 | ~$169 | award $194.77 [verified] |
| Cloud Solutions Architect | 15-1241+ | — | $90.00 | ~$190 | derived band $150–$230 [derived] |
| Sr Software Engineer | 15-1252 p75–90 | ~$80–95 [flagged pctile] | $84.00 | ~$178 | CALC upper $145–$166+; awards to $232 [verified] |
| Software Engineer | 15-1252 | $63.98 | $66.00 | ~$140 | CALC avg ~$124, range $82–$166 [verified] |
| Jr Software Engineer | 15-1252 p25 | ~$49–52 [derived] | $50.00 | ~$106 | CALC floor $82–$110 [verified] |
| DevSecOps Engineer | (no SOC) | mkt $77–100 sr [verified, non-BLS] | $78.00 | ~$165 | derived $140–$220 [derived — no CALC datum] |
| Cyber Analyst / ISSO | 15-1212 | $60.05 | $68.00 | ~$144 | band $115–$180; sr to $245 [verified] |
| Data Scientist | 15-2051 | $54.13 | $64.00 | ~$135 | award $165.90 (Health IT SIN) [verified] |
| Database Administrator | 15-1242 | $50.30 | $56.00 | ~$118 | awards $139.12–$146.16 [verified] |
| Business Analyst | 15-1211 proxy | $49.90 | $54.00 | ~$114 | awards $94.12–$132.72 [verified] |
| QA/Test Engineer | 15-1253 | $49.33 | $54.00 | ~$114 | award $106.15 [verified] |
| Technical Writer | 27-3042 | $44.07 | $48.00 | ~$102 | award $80.97 (mid) [verified] |
| Help Desk II | 15-1231 | $35.26 | $34.00 | ~$72 | derived $65–$100 [derived]; SCA governs |
| Help Desk I | 15-1232 | $29.01 | $28.00 | ~$59 | derived $45–$85 [derived]; SCA governs |

Empirical validation of the wrap: SWE median $63.98 × 1.85–2.2 = $118–$141,
matching the observed CALC SWE average of ~$124 [verified]. GSA schedule
rates are *ceilings*; task orders typically discount 5–15% below schedule.

Key sources: BLS OOH pages per SOC (bls.gov/ooh), Winvale CALC guide,
gsascheduleservices.com CALC blog, Fed-Spend 2026 labor-rate guide, awarded
MAS price lists (Dewberry, PDRI, CARS 47QTCA25D0062, Ivyhill, AMSG),
buy.gsa.gov. Full URL list retained in the research transcripts.

## 2. Indirect rates (basis for profile.yaml `indirect_rates`)

| Element | Advisory-consensus range | Meridian | Notes |
|---|---|---|---|
| Fringe | 25–40%, consensus mid ~30–32% [flagged: survey medians gated] | **31%** | BLS ECEC cross-check: benefits ≈ 42–43% of wages incl. statutory [verified] |
| Overhead (contractor-site) | 30–50% small services firm [verified advisory] | **36%** | keep <~35–40% to price competitively |
| Overhead (client-site) | ~10–25% [flagged: principle verified, numbers convention] | **18%** (policy) | separate site pool per EisnerAmper/GRF/BDO doctrine |
| G&A | 8–15%, mid 10–12% [verified advisory] | **11%** | TCI base |
| Fee (services) | FFP 7–15% proposed; CPFF 6–8% negotiated (10% statutory cap, FAR 15.404-4); realized SB net ~7.6–8% [verified] | **7%** | weighted-guidelines objectives ~8–12% [flagged] |
| Escalation | ECI 3.3–3.5% (Sep 2025–Mar 2026) [verified]; 2026 salary budgets 3.2–3.6% [verified]; offerors propose 2–3% commonly | **3.0%** | tie to ECI in the BOE; >4% invites realism pushback |

Wrap = 1.31 × 1.36 × 1.11 × 1.07 = **2.11** — inside the competitive
2.00–2.25 DC-metro small-business band; <2.0 reads aggressive, >2.3
uncompetitive in price-sensitive evaluations [verified advisory:
GovDash/Fed-Spend/Aprio/Redstone; broad band 1.6–2.2, full range 1.5–3.0].
Client-site wrap with the 18% site pool ≈ 1.73 (competitive band 1.6–1.8).

DCAA: 3-tier (fringe/OH/G&A-on-TCI) is the norm for service contractors;
2-tier acceptable for the smallest firms; provisional billing rates (FAR
42.704) submitted annually before FY start, trued up via the ICE.

Key sources: Deltek Clarity (16th, 2025) and Unanet/CohnReznick GAUGE
(2025/2026) press materials [rate tables gated — flagged], GovDash, Fed-Spend,
Aliff, Cabrillo Club, GRF, EisnerAmper, BDO, Cherry Bekaert, Aprio, Redstone
GCI, Warren Averett, CAVU, FAR 16.306 / DFARS 215.404-4, BLS ECI/ECEC,
WTW/Mercer/WorldatWork 2026 salary-budget surveys.

## 3. SCA/SCLS compliance floors (basis for content-sca-compliance)

- **H&W: $5.55/hr standard; $5.09/hr EO 13706 contracts** — DOL AAM 250,
  effective 2025-07-07 [verified]. Lineage: AAM 246 $5.36 (2024) → AAM 250
  $5.55 (2025). **Flag: check for a mid-2026 successor AAM before pricing.**
- **EO 14026 ($17.75) rescinded** 2025-03-14 by EO 14236 after the 9th Cir.
  struck it down (Nov 2024) and SCOTUS denied cert (Jan 2025); DOL is not
  enforcing [verified]. EO 13658 survives for pre-2022 legacy vehicles only:
  $13.30 (2025) → **$13.65 effective 2026-05-11** [verified, FR 2026-02466].
  Net: for current solicitations the WD rates themselves are the floor.
- **WD 2015-4281 (DC-MD-VA)**: Rev 33 (2025-04-25), Rev 34 (2025-07-08,
  first with $5.55), Rev 35 (2025-12-03, latest seen). Sourced line rates:
  General Clerk I/II/III $20.12/$21.96/$24.65 (Rev 33) [moderate confidence];
  14160 PC Support Technician and 14170 System Support Specialist rates
  could not be pinned to a revision [flagged — fetch live from
  sam.gov/wage-determination at runtime; revisions land 2–4x/year].
- **Occupation mapping**: help-desk titles are 14160 Personal Computer
  Support Technician / 14170 System Support Specialist (SCA Directory 5th
  ed.); computer operators 14041–45. Tier-1 help desk is SCA-covered
  regardless of rate (DOL FAB 2006-3); the FLSA 13(a)(17) computer
  exemption threshold is **$27.63/hr, not indexed** [verified].
- **Fringe standards**: vacation 2 wks @ 1 yr / 3 @ 5 / 4 @ 15 with
  successorship credit; 11 paid holidays; H&W + vacation + holidays
  separately owed (DOL FS 67B) [verified].
- **FAR 52.222-43**: new WD applies each option/anniversary; adjustment =
  actual wage/fringe delta + FICA/FUTA/SUTA/WC flow-through, **no OH/G&A/
  profit markup**; base prices must contain **no WD-escalation contingency**;
  30-day claim window [verified, acquisition.gov].

## 4. Estimating benchmarks (basis for content-estimating-benchmarks / boe-methodology)

- Service desk: desktop tickets/user/month 0.41–0.99 by industry
  (MetricNet/HDI) [verified]; **L2 113 tickets/tech/month avg, range 30–198**
  (MetricNet, 2014 vintage) [verified]; L1 ~555–560 contacts/agent/month
  figure comes from a MetricNet *sample* document [flagged — do not cite];
  utilization avg ~48%, sustained >60–70% drives turnover [verified]; FCR
  70–75% avg, 85%+ world-class [verified]; cost/ticket L1 ~$22, L2 ~$69,
  L3 ~$104 (2016–2020 vintage — inflation-adjust ~20–25%) [verified, dated].
- Sustainment: **15–25% of build cost annually** (business-critical 25–40%)
  [verified, multi-source]; team-shape ratios (1 PM : 2–4 pods, 1 DevOps :
  1–2 pods) are convention [flagged]; Scrum pod 3–9, typical ~7 [verified].
- Productive hours: 2,080 paid; **1,880–1,920 productive/billable standard**
  in federal pricing [verified advisory]; A-76's 1,776 figure [flagged].
- ODC: no defensible %-of-contract benchmark exists [flagged — build
  bottom-up]; GSA OLM cap 33.33% of order value [verified]; **FY2026 CONUS
  per diem $178 ($110 + $68 M&IE)**, first/last day 75% [verified, FR
  2025-15771 / FTR Bulletin 26-01].
- Competition: avg **3.6 offers/solicitation (FY2024)** → baseline P(win)
  ~28% [verified]; LPTA restricted for IT/knowledge-based services (DFARS
  2019, FAR 2021) and declining [verified]; best-value premiums usually
  single-digit % (GAO-11-8: 12 of 21 premiums ≤5%) [verified]; FY2025 GAO
  protests: 1,688 filed, 14% sustained, 52% effective [verified].
- CPARS: Satisfactory share rose 2014–2018 while Exceptional/Very Good
  declined (FNN/Jason Miller) [verified]; exact distribution percentages
  gated (HigherGov) [flagged]; Satisfactory maps to mid-tier confidence in
  past-performance evaluations — eligible but rarely winning.
- B&P: **~1.5% of contract value** to prepare a competitive proposal
  [verified, OCI/GovDash]; disciplined gate reviews ⇒ 2× likelihood of >50%
  win rate (APMP) [verified]; cross-industry RFP win rate ~45% (2025,
  blends recompetes) [verified].

## Standing flags (re-verify before relying)

1. Possible successor to AAM 250 (H&W) issued mid-2026.
2. WD 2015-4281 revision > 35 and its 14160/14170 line rates — fetch live.
3. Survey medians for fringe/OH/G&A (Deltek Clarity, GAUGE) — gated;
   advisory-consensus ranges used instead.
4. MetricNet current (2024+) benchmarks — paywalled; public figures are
   2014–2020 vintage and inflation-adjusted here.
5. DC-metro per-SOC wage premiums — computed from the all-occupation
   premium, not per-SOC rows.
6. DevSecOps / Cloud Architect / Help Desk GSA awarded rates — no primary
   CALC datum surfaced; bands are derived.
