# Competitive benchmark — scored set (stage 2 of 3)

Scored 2026-07-30 per the `benchmark-methodology` skill, against the stage-1 scoped set in
`docs/COMPETITIVE_LANDSCAPE.md`. All eight direct/adjacent rivals scored 1–5 on nine
dimensions calibrated to BidPilot's positioning brief (trust/compliance depth weighted over
prose quality). **No composite score is reported** — the asymmetries are the findings.
Hand-off: `competitive-report-structure` (stage 3).

**Sourcing note.** Vendor sites block direct page fetching from this environment (HTTP 403),
so evidence was collected via domain-restricted search over each vendor's own pages
(vendor-authored primary content), with the FedRAMP tier claims cross-checked against
FedRAMP-mechanics coverage. Third-party sources were used only for price points and
funding/customer facts and are labeled *(third-party)*. Scores resting on thin or
conflicting evidence are marked **[unverified]**.

**Corrections to stage 1 discovered during verification:**

- **GovEagle "pricing/cost-volume guidance" is content marketing, not product.** Its cost-volume
  material is educational blog guides; no pricing-automation feature exists on its platform page.
- **GovDash is FedRAMP *Ready*** (3PAO readiness assessment, building on Moderate *Equivalency*),
  not High as some third-party comparisons imply.
- **GovSignals' and AutogenAI's FedRAMP High both ride Palantir FedStart's authorized
  environment**, not independent agency ATOs; Procurement Sciences' FedRAMP Moderate (Mar 2026)
  is inherited from the Knox Systems boundary. Real, but rented — this matters for the white-space.

## Rubric anchors (calibrated)

1 = absent/no evidence · 2 = mentioned, thin or conflicting evidence · 3 = competent
table-stakes, asserted · 4 = strong, mechanism described on vendor's own pages · 5 = category
best in this set, corroborated. Dimension 6 scores the **verified tier**, not the marketing claim:
5 = FedRAMP High authorized environment · 4 = FedRAMP Moderate Authorized · 3 = Moderate
Equivalency/Ready + CMMC L2/SOC 2 · 2 = SOC 2/self-attestation only · 1 = none.

## Master scoring grid (8 rivals x 9 dimensions)

| Dimension | Sweetspot | GovDash | Vultron | Awarded.ai (PSci) | pWin.ai | GovSignals | AutogenAI Fed | GovEagle |
|---|---|---|---|---|---|---|---|---|
| 1. Discovery breadth | **5** | 4 | 2 | **5** | 2 | 4 | 1 | 1 |
| 2. Solicitation analysis depth | 4 | 4 | 4 | 4 | **5** | 3 | 4 | 4 |
| 3. Drafting quality + grounding | 3 | 4 | 4 | 3 | **5** | 3 | 4 | 4 |
| 4. Pricing / cost-volume automation | 1 | **5** | 1 | 2 [unverified] | 1 | 1 | 1 | 1 |
| 5. Verifiable trust mechanisms | 3 | 3 | 3 [unverified] | 2 | **4** | 3 | 3 | 3 |
| 6. Security certifications (verified tier) | 3 | 3 | 2 [unverified] | 4 | 3 | **5** | **5** | 3 |
| 7. Lifecycle breadth | 4 | **5** | 3 | 4 | 3 | 4 | 2 | 2 |
| 8. Pricing / accessibility (10–100 firms) | **5** | 3 | 2 | 3 | 2 | 4 | 2 | 3 [unverified] |
| 9. Win-probability / price-to-win intel | 4 | 4 | 3 | 4 | 4 | 3 | 1 | 1 |

## Evidence per rival (one-line justification + source per score)

### Sweetspot — Direct; closest architectural mirror
- **D1: 5** — Enumerated multi-source engine: SAM.gov, USAspending, FPDS, DIBBS, Grants.gov, 1,000+ SLED sources with set-aside filtering — https://www.sweetspot.so/features/opportunity-discovery/
- **D2: 4** — Shreds RFPs into compliance matrices, capability matrices, and team assignments — https://www.sweetspot.so/features/proposal-engine/
- **D3: 3** — Pink-team drafts from company content; grounding asserted as "sourced and traceable" with less mechanism detail than peers' per-claim citation — https://www.sweetspot.so/features/
- **D4: 1** — No pricing/cost-volume module anywhere on the features pages — https://www.sweetspot.so/features/
- **D5: 3** — RBAC, SSO, "full audit trails" claimed; citations traceable but advisory — nothing enforced or blocking — https://www.sweetspot.so/sweetspot-versus-other-govcon-software/
- **D6: 3** — SOC 2 Type II + CMMC Level 2 claimed; "FedRAMP Moderate deployment" is a hosting posture, not an authorization — https://www.sweetspot.so/sweetspot-versus-other-govcon-software/
- **D7: 4** — Discovery -> pipeline -> proposal plus contract/competitor monitors; no true post-award management — https://www.sweetspot.so/features/pipeline-management/ , https://www.sweetspot.so/features/monitors/
- **D8: 5** — Entry ~$500/yr single user, Standard $2,500, Leader $5,000 *(third-party corroboration; site pricing page is demo-gated)* — https://www.sweetspot.so/pricing/ , https://bidsparq.com/vs/sweetspot
- **D9: 4** — Bid/no-bid recommendation with win probability and gap analysis; fit-scoring, no price-to-win — https://www.sweetspot.so/govcon-workflows/sled-capture-analysis/

### GovDash — Direct; category-leader posture
- **D1: 4** — Discover pulls SAM.gov, PIEE, SLED (public tab), GovWin integration, private portals (eBuy) with AI bid-match scoring — https://support.govdash.com/docs/sled-in-discover , https://support.govdash.com/docs/govwin-integration
- **D2: 4** — Shred to compliance/capability matrices mapped to past performance, linked to Data Library — https://support.govdash.com/docs/capability-matrices
- **D3: 4** — "Cites every claim back to your Data Library"; the clearest per-claim grounding statement in the direct set — https://www.govdash.com/data-library
- **D4: 5** — Pricer (Mar 2026): extracts CLINs/LCATs from solicitation sections, wrap-rate calculations, cost build-ups, BOE narrative pulled from Data Library — the only real cost-volume automation in the set — https://www.govdash.com/blog/writing-cost-volume-government-proposal
- **D5: 3** — Citation-to-source mechanism described, but advisory (reviewer can check) — no enforcement, no determinism claims — https://www.govdash.com/data-library
- **D6: 3** — FedRAMP **Ready** (3PAO readiness assessment) on Moderate Equivalency posture; Azure GovCloud; CMMC-aligned, not certified — https://www.govdash.com/blog/govdash-earns-fedramp-ready-and-joins-fedramp-marketplace
- **D7: 5** — Five modules Sourcing -> Capture -> Pricer -> Proposal -> Contract (post-award: CPARS, mods, deliverables); widest lifecycle in the set — https://www.govdash.com/
- **D8: 3** — No public pricing; flat-rate/no seat fees; ~$3k/month reported quote *(third-party)* — https://www.govdash.com/pricing , https://www.saasworthy.com/product/govdash/pricing
- **D9: 4** — AI scoring ranks opportunities against past performance, capabilities, and win history; PTW exists only as content guides — https://support.govdash.com/docs/finding-opportunities-using-bid-match

### Vultron — Direct; contests the custom-LLM moat
- **D1: 2** — No native multi-source discovery engine; scores solicitations the customer brings — https://www.vultron.ai/platform/workflows
- **D2: 4** — Auto-extracts requirements, deadlines, compliance obligations; AI compliance matrices and Pink Team reviews — https://www.vultron.ai/platform/proposal-ai
- **D3: 4** — Proprietary models trained on licensed winning-proposal data + citation-backed answers; "95%+ compliance accuracy" is asserted without methodology — https://www.vultron.ai/ , https://www.builtinsf.com/articles/vultron-raises-22m-20250716 *(third-party)*
- **D4: 1** — No pricing/cost-volume capability on any platform page — https://www.vultron.ai/platform/workflows
- **D5: 3 [unverified]** — "Citation-backed" drafting plus a public trust center, but the trust center proves security posture, not output correctness — https://security.vultron.ai
- **D6: 2 [unverified]** — Site claims SOC 2 I/II, NIST 800-171, CMMC L2 and "FedRAMP-compliant infrastructure" (not authorization); third-party checks found no public FedRAMP/CMMC listing — conflicting evidence — https://www.vultron.ai/security
- **D7: 3** — Solicitation analysis -> opportunity scoring -> drafting -> color-team reviews; no discovery, no post-award — https://www.vultron.ai/platform/workflows
- **D8: 2** — Fortune 500/defense-prime orientation, 400+ contractors, no public pricing — https://www.vultron.ai/customers
- **D9: 3** — Evaluates solicitations against past performance to prioritize "highest probability of success"; no calibrated PWin or PTW — https://www.vultron.ai/platform/workflows

### Procurement Sciences (Awarded.ai) — Direct; nearest to BidPilot's agent roster
- **D1: 5** — HigherGov acquisition folds in federal + SLED + grants aggregation, bid forecasts, recompetes, award histories, labor-pricing benchmarks — https://www.procurementsciences.com/blog/procurement-sciences-acquires-highergov-one-platform-to-find-and-win-government-contracts
- **D2: 4** — Automated compliance matrices "90% faster"; own blog candidly notes first-pass matrices need human validation for Section L/attachments/amendments — https://www.procurementsciences.com/blog/compliance-matrix
- **D3: 3** — Multi-agent drafting/outlines grounded in company content; citation mechanism not publicly specified — https://www.procurementsciences.com/platform
- **D4: 2 [unverified]** — "Pricing" named in the lifecycle claim but no module detail found; HigherGov labor-pricing benchmarks are data, not automation — https://www.procurementsciences.com/solutions/government-contracting
- **D5: 2** — Weakest public evidence in the set for citation enforcement or AI-output audit trails — https://www.procurementsciences.com/platform
- **D6: 4** — FedRAMP **Moderate Authorized** (Mar 2026, inherited via Knox Systems boundary) + SOC 2 Type 2 + on-prem options — https://www.procurementsciences.com/blog/why-fedramp-authorization-and-cmmc-level-2-are-now-table-stakes-for-govcon-ai
- **D7: 4** — Discovery -> capture -> proposal -> compliance -> pricing -> win analysis; "delivery" asserted, post-award evidence thin — https://www.procurementsciences.com/platform
- **D8: 3** — No public pricing; serves 3,000+ contractors including small businesses, but authorization-tier products price up — https://www.prnewswire.com/news-releases/procurement-sciences-acquires-highergov-creating-the-largest-ai-powered-growth-platform-in-government-contracting-302776364.html *(third-party)*
- **D9: 4** — "Win analysis" plus HigherGov's award-history and labor-pricing benchmark data — the best PTW *raw material* in the set — https://www.procurementsciences.com/blog/procurement-sciences-acquires-highergov-one-platform-to-find-and-win-government-contracts

### pWin.ai — Direct; contests the PWin lane
- **D1: 2** — No native discovery; opportunity intel arrives via TechnoMile/market-intelligence integrations — https://www.pwin.ai/partnerships/
- **D2: 5** — Patented engine reads the solicitation, builds an annotated outline, maps requirements to sections; deepest shred-to-structure claim in the set — https://www.pwin.ai/solutions/
- **D3: 5** — Shipley co-developed writing engine; 40+ structured prompts per section with an agentic execute-evaluate-refine loop to Shipley-quality standards — https://www.pwin.ai/about/ , https://www.pwin.ai/solutions/
- **D4: 1** — Despite the name, no cost-volume or price-to-win automation is evidenced — https://www.pwin.ai/solutions/
- **D5: 4** — Automated **hallucination, citation, and compliance reports with every draft** — the only vendor shipping verification artifacts; still advisory, not export-blocking — https://www.pwin.ai/solutions/
- **D6: 3** — FedRAMP Moderate **Equivalency** (3PAO-assessed), CMMC L2 deployment in Azure Gov, NIST 800-171, dedicated enclaves — https://www.pwin.ai/why-pwin-ai/
- **D7: 3** — Capture-data extraction from CRM through proposal generation; no discovery, no post-award — https://www.pwin.ai/solutions/
- **D8: 2** — Enterprise motion (MACC-fundable via Azure commitment); no public pricing — https://www.pwin.ai/
- **D9: 4** — Auto-scores RFPs against historical success and capabilities; fit-based PWin, no price-to-win math — https://www.pwin.ai/why-pwin-ai/

### GovSignals — Direct; intel-led converging on drafting
- **D1: 4** — Source-cited intelligence across federal, SLED, budget, award, forecast, and agency signals incl. pre-RFP; breadth asserted rather than enumerated — https://www.govsignals.ai/works/government-opportunity-intelligence/
- **D2: 3** — Extracts risks, requirements, program terms with auto-compliance checks; less evidence of L/M-structured matrix artifacts than peers — https://www.govsignals.ai/
- **D3: 3** — Compliant drafts "in under 30 minutes"; drafting is the newer half of the product — https://www.govsignals.ai/works/government-proposal-writer/
- **D4: 1** — No cost-volume automation evidenced — https://www.govsignals.ai/
- **D5: 3** — Agents "hand off with the citations to prove it"; source-cited outputs traceable to notices/awards/records — described, not enforced — https://www.govsignals.ai/
- **D6: 5** — FedRAMP **High** authorized environment (Nov 2025, via Palantir FedStart) — the top verified tier in the set; note it is an inherited enclave, not an independent ATO — https://www.govsignals.ai/compliance/fedramp-high
- **D7: 4** — "Pre-award to post-award" positioning: market research -> solicitation -> compliance -> award analysis -> procurement reporting — https://www.govsignals.ai/
- **D8: 4** — Published module ranges ($5k–25k/yr AI proposal modules) and named small-business plans; flat annual engagement — https://www.govsignals.ai/pricing/ , https://softwarefinder.com/artificial-intelligence/govsignals *(third-party)*
- **D9: 3** — Flags bids aligned to strategy with daily recommendations; no calibrated PWin or PTW — https://www.govsignals.ai/

### AutogenAI Federal — Adjacent; the compliance ceiling reference
- **D1: 1** — No discovery module; the product begins at the RFP — https://autogenai.com/federal/
- **D2: 4** — Smart Compliance + RFP Shredding; every shall/must/will tracked from extraction to final draft with Gamma Review checking — https://autogenai.com/federal/
- **D3: 4** — Evidence-backed drafting with traceable citations via RAG and semantic tagging from internal libraries; "241% win-rate increase" is asserted marketing — https://autogenai.com/blog/press-release-autogenai-federal/
- **D4: 1** — No pricing/cost-volume automation — https://autogenai.com/federal/
- **D5: 3** — "Full audit trail" + requirement traceability claimed; no public proof artifacts or enforcement — https://autogenai.com/federal/
- **D6: 5** — FedRAMP **High** boundary on AWS GovCloud via Palantir FedStart; IL5/IL6-aligned design, CMMC 2.0, ISO 27001, SOC 2; same inherited-enclave caveat as GovSignals — https://autogenai.com/federal/
- **D7: 2** — Proposal-stage depth only; no discovery, capture, pricing, or post-award modules — https://autogenai.com/federal/
- **D8: 2** — All-inclusive single-number contract (all users/modules/environments) is enterprise-shaped; small-business page exists but no pricing disclosed — https://autogenai.com/federal-small-business-proposal-software/
- **D9: 1** — No win-probability or PTW capability evidenced — https://autogenai.com/federal/

### GovEagle — Adjacent; SMB proposal specialist
- **D1: 1** — No discovery capability; proposal-stage product — https://www.goveagle.com/platform
- **D2: 4** — Shreds all solicitation docs into an Excel compliance matrix aligned to Section L/M with instructions, eval criteria, task areas — https://www.goveagle.com/platform
- **D3: 4** — Compliant pink-team draft "in your voice, your template, with full citations to your source material"; native Word add-in workflow — https://www.goveagle.com/platform
- **D4: 1** — Stage-1's "cost-volume guidance" is educational blog content, not a product feature; no pricing automation exists — https://www.goveagle.com/blog/pricing-cost-volumes-guide
- **D5: 3** — Full citations so managers "can verify every claim" — human-verifiable, machine-unenforced — https://www.goveagle.com/blog/ai-proposal-writing-tools-government-contractors
- **D6: 3** — FedRAMP Moderate **Equivalency** in AWS GovCloud; GCC High support; self-hosted and air-gapped deployment options — https://www.goveagle.com/blog/secure-ai-platforms-government-proposal-data
- **D7: 2** — Shred -> draft -> review only — https://www.goveagle.com/platform
- **D8: 3 [unverified]** — No public pricing; own positioning targets 50–2,000+ employee firms, weakening stage-1's "SMB-priced" claim — https://www.goveagle.com/blog/govdash-reviews-pricing-alternatives
- **D9: 1** — PWin exists only as an educational guide; no product feature — https://www.goveagle.com/blog/what-is-pwin-probability-of-win-guide

## Tension plot — automation depth x verifiable trust

**X = automation depth** (how much of discover -> shred -> draft -> price -> assemble runs
without a human doing the work). **Y = verifiable trust** (what the system *proves* about its
output: enforced citations, deterministic computation, verification artifacts, audit trails —
not what it claims). Midpoint 3. BidPilot's target quadrant is **high/high**.

| Rival | Automation depth | Verifiable trust | Quadrant |
|---|---|---|---|
| Sweetspot | 4 | 3 | High automation / borderline trust |
| GovDash | 5 | 3 | High automation / borderline trust |
| Vultron | 4 | 3 | High automation / borderline trust |
| Awarded.ai | 4 | 2 | High automation / low trust |
| pWin.ai | 4 | 4 | **High/high (advisory tier)** |
| GovSignals | 3 | 3 | Center |
| AutogenAI Fed | 3 | 3 | Center (trust = environment, not output) |
| GovEagle | 3 | 3 | Center |
| **BidPilot (target)** | **5** | **5** | **High/high (enforced tier)** |

- **Upper-right (high/high):** Occupied only by **pWin.ai (4,4)** — the single most important
  finding of this benchmark. Its per-draft hallucination/citation/compliance reports are real
  verification artifacts. But they are *advisory*: a human reads the report and decides. Nothing
  blocks a bad export, nothing is deterministic, and it has no discovery or pricing automation.
  The **enforced** corner of the quadrant — citations gate the build, arithmetic never touches an
  LLM, halts re-evaluated not bypassed — is empty.
- **Lower-right (deep automation, borrowed trust):** GovDash (deepest pipeline, incl. the only
  cost-volume automation), Sweetspot, Vultron, Awarded.ai. All ground drafts in company content
  and describe citations; none prove enforcement. Awarded.ai automates the most agents with the
  least public trust machinery.
- **Upper-left is empty:** nobody sacrifices automation for verifiability — trust is universally
  a marketing layer on the same RAG architecture.
- **Center (trust = certificates):** GovSignals and AutogenAI hold the top *security* tier
  (FedRAMP High, both via Palantir FedStart enclaves), but environment security is not output
  verifiability — a FedRAMP High boundary will host a hallucinated past-performance claim just as
  reliably as a compliant one. GovEagle keeps trust human-shaped (verify in Word).

## White-space statement

**Nobody in the market machine-enforces truth; they all sell the ability to check it.**
Every rival grounds drafts in company content and emits citations a human *may* verify; pWin.ai
alone ships verification reports, and even those are advisory. No vendor blocks export on an
uncited claim, computes pricing/wage-determination/page-count arithmetic outside an LLM, or
guarantees never-sign/never-submit. Meanwhile the trust the market does sell — FedRAMP tiers —
is (a) rented (Palantir FedStart, Knox boundary inheritance) and (b) priced for enterprises,
leaving 10–100 person set-aside holders choosing between affordable tools with asserted trust
(Sweetspot at ~$500–5k/yr) and trusted environments they can't afford (AutogenAI, GovSignals
primes tier). The open position is **enforced verifiability at small-firm prices**: fail-closed
citation gating, deterministic computation, exportable proof — the high/high quadrant's enforced
tier, currently occupied by no one.

## Top-5 "exploit this gap" recommendations for BidPilot

1. **Ship the Trust Manifest — turn fail-closed enforcement into an artifact rivals can't fake.**
   pWin.ai's per-draft reports prove buyers want verification evidence; nobody makes it binding.
   Export a machine-readable manifest with every package: every claim -> KB entry ID, every number
   -> deterministic code path (`pricing/rates.py`, page counts), every LLM call -> audit log entry,
   plus the QA gate results that *blocked or passed* the build. This productizes what
   `qa_checks.citation_check` and `audit.py` already do and converts "we don't hallucinate"
   from a claim every rival makes into a document only BidPilot can produce.

2. **Attack GovDash Pricer head-on with deterministic pricing.** GovDash is the only rival with
   cost-volume automation, and it's LLM-assisted narrative + calculation in one product with no
   determinism guarantee. BidPilot's lane: wrap rates, escalation, and WD floors as pure,
   unit-tested code with published eval-harness accuracy — market it as "your cost volume can
   survive DCAA because no model ever touched the math." Add CLIN/LCAT extraction parity so the
   comparison is feature-for-feature, then win on verifiability.

3. **Counter rented FedRAMP with the self-hosted trust tier.** The set's two "FedRAMP High"
   vendors both inherit Palantir FedStart enclaves and price for primes; the Moderate tier is
   inherited (Knox) or equivalency-only. BidPilot can't out-certify them soon — so invert the
   axis: your keys, your machine, your custom model, full audit trail, zero data leaves the
   boundary you already own. For a 50-person firm handling CUI, local deployment is both cheaper
   and a stronger data-control story than someone else's enclave. Publish transparent usage
   pricing (~$25/run ceiling) beside it — only Sweetspot and GovSignals publish anything, and
   demo-gated pricing is a known SMB irritant.

4. **Close the discovery gap to table-stakes: DIBBS + Grants.gov + top SLED sources.** Discovery
   breadth is the market's loudest checkbox (Sweetspot's 1,000+ sources; Awarded.ai buying
   HigherGov outright), and SAM-only intake is BidPilot's most visible scored deficit (would score
   1–2 on its own grid). Source plurality behind the existing `intake/` interface — DIBBS and
   Grants.gov first, SLED aggregation second — neutralizes the objection without chasing
   Sweetspot's total count; recall-over-cost classification is already the harder, built half.

5. **Win the PWin lane with evidence-gated scoring.** Four rivals sell fit-based PWin
   (Sweetspot, GovDash, Awarded.ai, pWin.ai); none publish calibration and none refuse to answer
   when evidence is thin — a score always appears. BidPilot's outcome-trained P(win) plus
   FPDS-grounded price positioning should visibly *decline to serve* below an evidence threshold
   and show its inputs when it does serve. "The PWin that tells you when it doesn't know" is the
   only version of this feature consistent with the trust brand — and Awarded.ai's HigherGov
   pricing-benchmark data shows where the PTW raw material lives if a data partnership is ever
   on the table.

---

*Verification limits: vendor pages were read via search index rather than direct fetch (sites
block automated fetching); price points marked (third-party) were not confirmed on vendor pricing
pages, which are demo-gated. FedRAMP claims reflect vendor statements cross-checked against
FedRAMP-mechanics coverage as of 2026-07-30 — re-verify against marketplace.fedramp.gov before
external publication. Scores marked [unverified] rest on thin or conflicting evidence and should
not be quoted without that flag.*