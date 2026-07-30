# Competitive landscape — scoped set (stage 1 of 3)

Scoped 2026-07-29 per the competitive-platform-analysis method: positioning
brief -> axis population -> pre-filter -> tiered set. Scoring hands off to
benchmark-methodology (stage 2). Sources: vendor sites and comparison posts
verified across at least two of GovDash, Sweetspot, GovEagle, AutogenAI,
Procurement Sciences, and Civio publications (each has obvious self-interest;
attributes were only kept when corroborated).

## Positioning brief (from the PRD — BidPilot's own lens)

- **Identity**: engineering-first agentic pipeline; trust posture is the brand
  ("fail-closed" over "magic").
- **Offer**: SAM.gov listing URL -> analyzed, priced, QA'd, submission-ready
  package with human gates; deterministic pricing/WD compliance in code.
- **Target**: small federal contractors (10-100 staff, set-aside holders)
  without dedicated proposal shops.
- **Differentiator**: verifiable trust — KB-cited claims or the build fails,
  arithmetic never touches an LLM, never-sign/never-submit, full audit trail,
  exact page counts, ~$25/run cost ceiling, self-improving custom-model loop.
- **Scoping consequence**: weight rivals by TRUST/COMPLIANCE DEPTH and
  lifecycle automation, not prose quality — everyone claims good writing.
- **Strategic tension**: automation depth x verifiable trust (deeper
  automation usually means more hallucination surface; the moat is claiming
  both).

## Tiered set (11 candidates -> 8 to profile)

### Direct (same buyer, overlapping automation ambition)

| Candidate | Stance | Signal attributes (corroborated) | Overlap | Distinct. | Cred. | Profile? |
|---|---|---|---|---|---|---|
| **Sweetspot** | capability-led, full capture->proposal | multi-source discovery (SAM, DIBBS, grants, 1,000+ SLED), shred->matrix->draft from company content, CUI handling certs | 5 | 4 | 4 | MUST — closest architectural mirror |
| **GovDash** | lifecycle-led (YC) | pipeline + gate reviews + teaming + post-award in one; strongest workflow breadth | 4 | 4 | 5 | MUST — category leader posture |
| **Vultron** | model-led | proprietary models trained on licensed proposal data; high-volume drafting speed | 4 | 4 | 4 | MUST — contests the custom-LLM moat |
| **Procurement Sciences (Awarded.ai)** | agent-led | multi-agent (tracking, matrix, strategy, drafting); FedRAMP Moderate | 5 | 3 | 4 | yes — nearest to BidPilot's agent roster |
| **pWin.ai** | method-led | Shipley-structured drafts; PWin analysis off historical data | 4 | 3 | 3 | yes — contests the P(win)/price-position lane |
| **GovSignals** | intel-led expanding to drafting | FedRAMP High; solicitation analysis + drafts | 3 | 3 | 4 | yes — intel->drafting convergence proof |

### Adjacent (pressures at the edges)

| Candidate | Why it matters | Profile? |
|---|---|---|
| **AutogenAI Federal** | enterprise trust bar: FedRAMP High on GovCloud/Palantir, CMMC 2.0, IL5 alignment, all-inclusive pricing — where security-first buyers land | yes — the compliance ceiling reference |
| **GovEagle** | pricing/cost-volume guidance + proposal automation content engine; SMB-priced | yes |
| Deltek GovWin IQ / HigherGov | market-intelligence incumbents feeding every rival's discovery layer | landscape note only |
| Responsive / Loopio | commercial RFP response suites drifting toward govcon | landscape note only |

### Aspirational

- **GovDash doubles here**: its full-lifecycle breadth (capture through
  post-award contract management) is the commercial-maturity bar, even though
  it competes directly today.

### Substitutes (threat vector, not profiles)

Raw ChatGPT/Claude + templates (the real competitor for most 10-person
shops); Shipley-trained consultants; hiring a proposal manager.

## What the set already says about making the product better

1. **Security posture is a table stake, not a feature.** The market
   discriminates on FedRAMP High vs Moderate vs self-attestation; BidPilot has
   real audit machinery but zero certifications. The self-hostable/local
   deployment story (your keys, your machine, your custom model) is the
   asymmetric answer small primes can act on today — say it loudly.
2. **Discovery breadth gap**: Sweetspot's 1,000+ SLED/DIBBS/grants sources vs
   BidPilot's SAM-only intake. The `discover` layer needs source plurality on
   the roadmap.
3. **Nobody verifiably solves hallucination** — competitors ground drafts in
   company content; none advertise fail-closed citation enforcement that
   BLOCKS export, deterministic pricing, or never-sign guarantees. That is the
   white-space at the strategic tension; benchmark stage 2 should score it
   hard.
4. **Post-award is the expansion axis** (GovDash proves the buyer wants one
   system); out of scope for v1 but belongs on the roadmap conversation.
5. **PWin/price intelligence is contested** (pWin.ai): BidPilot's outcome-
   trained P(win) + FPDS price positioning is directionally right — the moat
   is that ours gates itself on evidence before serving.

Next stage: `benchmark-methodology` — score the 8 profiled candidates across
the nine weighted dimensions with the automation-depth x verifiable-trust
tension plot.
