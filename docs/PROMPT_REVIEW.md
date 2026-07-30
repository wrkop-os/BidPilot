# Prompt Review — pre-live-run audit

**Status: REPORT ONLY.** No prompt has been changed. Every proposal below is
gated on an eval that proves it helps (per the "new failure -> corpus item +
test before the fix" rule). Binding target: **G2, >= 98% requirement recall in
the shredder** (`evals/harness.py`). Secondary targets: draft factuality
(FR-10 claims discipline) and QA finding precision.

Scope reviewed: every system string and user-prompt template in
`bidpilot/agents/{shredder,writers,qa,classification,eligibility,architect,
submission,past_performance,forms,pdf_forms,capability}.py` and
`bidpilot/pricing/{estimator,boe}.py`, read against the Pydantic schemas in
`bidpilot/models.py` / `bidpilot/pricing/models.py` and the downstream code
that consumes each output (`dedup_requirements`, `assign_ids`,
`qa_checks.coverage_check`, `qa_checks.citation_check`).

Findings are ranked by expected recall/factuality impact. Each has:
(a) the weakness quoted, (b) a full replacement for the weak section,
(c) the named failure mode it prevents, (d) the eval that would prove it.

---

## 1. Shredder Pass 1 — `EXTRACT_SYSTEM` (`bidpilot/agents/shredder.py:31`)

This is the primary recall surface (fast tier, per window — where volume
lives), so it gets the most explicit guidance.

### F1 (CRITICAL, recall) — the trigger-phrase list acts as a lexical filter

**(a) Weakness:**

> Extract EVERY imperative aimed at the offeror or the contractor: "shall", "must",
> "will provide", "is required to", "not to exceed", "shall be organized as follows",
> page limits, formatting rules, submission rules, required forms, evaluation
> criteria descriptions.

The word "imperative" plus a short cue list teaches a Haiku-class model to
grep for those cues. Real solicitations bind offerors without them:
conditional disqualifiers ("failure to acknowledge amendments will result in
rejection"), negative-consequence phrasing ("proposals exceeding 10 pages
will not be read"), deliverable/CDRL tables with no verb at all, and
"should"-phrased instructions evaluators treat as mandatory. Nothing tells
the model the list is non-exhaustive, that table rows count, or what NOT to
skip.

**(b) Proposed replacement (entire `EXTRACT_SYSTEM`):**

```
You extract binding requirements from federal solicitation text. Your recall
target is 98%+: a missed requirement can disqualify the proposal, while an
extra row costs nothing (duplicates are removed deterministically downstream).
When unsure whether something is binding, ALWAYS extract it.

Extract every obligation on the offeror or the contractor, in whatever form
it appears. Trigger language includes but is NOT limited to:
- imperatives: "shall", "must", "will provide", "is required to", "agrees to";
- prohibitions and caps: "shall not", "not to exceed", "no more than", "will
  not be accepted/considered/evaluated";
- conditional disqualifiers: "failure to X will result in ...", "proposals
  that do not X will be rejected / deemed non-responsive";
- structural mandates: "shall be organized as follows", page limits, fonts,
  margins, file formats, naming conventions, copies;
- administrative/submission mechanics: registrations, portals, required
  forms, certifications, deadlines, subject lines, attachment size limits;
- evaluation criteria descriptions (how the government scores);
- deliverables and due dates, INCLUDING those stated only in tables
  (CDRL-style rows, deliverable schedules, pricing-workbook instructions) —
  extract each table row that binds the offeror as its own requirement;
- "should"/"may" statements a reasonable offeror would treat as expected by
  evaluators — extract them and record the softer verb in notes.

Do NOT skip a requirement because:
- it appears in a table, footer, attachment excerpt, or amendment text;
- it is cut off at the start or end of the provided text (quote the fragment
  verbatim; an overlapping window captures the rest);
- it looks minor, obvious, or like standard boilerplate;
- it repeats something you already extracted (dedup is handled in code).

Field rules:
- verbatim_text: quote the requirement sentence(s) exactly. Never paraphrase
  away binding language. Split compound sentences into separate requirements.
- source: the document name is given; capture the section (L/M/C/PWS number)
  and the [page N] marker nearest above the text. If no section header or
  page marker is visible in this text, leave section/page null — never guess.
- category: format (fonts/pages/files) | content (what the proposal/work must
  contain) | administrative (registration, forms, submission mechanics) |
  evaluation (how the government scores).
- req_id: use the solicitation's own numbering where present (e.g. 'L-4.2.1',
  'PWS 3.1.5'); otherwise leave req_id as 'TBD' — IDs are assigned later.
```

**(c) Failure mode prevented:** missed requirements that lack imperative
verbs — the single most likely cause of a G2 miss on a real solicitation
(disqualifiers and table-borne deliverables are exactly the rows a
cue-matching pass drops).

**(d) Eval:** the existing `evals/harness.py` recall metric, but with the
gold CSVs sliced: tag each gold row `has_modal_verb` (contains
shall/must/required) vs not, and `in_table` vs not. Report recall per slice
before/after. The rewrite is proven if no-modal-verb and in-table slices
improve without overall-recall regression. The corpus_demo gold matrix plus
the 5 planned hand-built matrices are sufficient.

### F2 (HIGH, recall) — no window-boundary contract in the user prompt

**(a) Weakness:** the per-window user prompt is just:

> `f"Document: {doc.name}\n\n{window}"`

Windows are 60,000 chars with 8,000 overlap, so every window after the first
starts mid-sentence/mid-section and the model is never told. Two concrete
risks: (1) an edge-truncated requirement looks like garbage and gets
skipped — the overlap only rescues it if the *other* window's model extracts
it; (2) the instruction "capture the section ... nearest above the text" is
unsatisfiable for the top of later windows (the header may be 30k chars
back), inviting either guessed sections or skipped rows.

**(b) Proposed replacement (user-prompt template in `shred()`):**

```
prompt=f"""Document: {doc.name}
Window {idx} of {total} (chars {start}-{start + len(window)} of {len(text)}).
This window may begin or end mid-sentence or mid-section. Adjacent windows
overlap, so: extract requirements truncated at either edge verbatim as
fragments rather than skipping them, and if the governing section header is
not visible in this window, leave source.section null rather than guessing.

{window}"""
```

**(c) Failure mode prevented:** window-edge requirement drops (a miss class
invisible in small test docs and guaranteed to appear on 100+ page
solicitations), plus fabricated section attributions in later windows.

**(d) Eval:** synthetic boundary corpus — generate a document of filler with
known requirements planted exactly at every `_window_starts` boundary
(straddling the cut) and mid-window controls; assert 100% recall on both
groups via the harness matcher. Deterministic to construct; run per prompt
variant.

---

## 2. Shredder Pass 3 — `ADVERSARIAL_SYSTEM` (`shredder.py:49`)

This is the G2 backstop: whatever Pass 1 misses, only this pass can recover.

### F3 (CRITICAL, recall) — the checklist anchors the sweep, and "missing" is undefined

**(a) Weakness:**

> Find binding requirements that are MISSING from the matrix: buried
> formatting rules, submission mechanics, required forms, certifications, page
> limits, org-of-proposal mandates, evaluation gate criteria, wage determination
> obligations. Return ONLY the missing requirements ... If truly nothing is
> missing, return an empty list — but look hard first; misses are catastrophic.

Three problems. (1) The named categories are all format/admin — the model
will hunt those and under-search PWS/technical content, attachments, and
Section M subfactors. (2) No search *procedure* is prescribed, and the
structured output (`_ExtractedRequirements`, default empty list) gives the
model a zero-effort exit. (3) "Missing" is left to the model's judgment: when
a matrix row *partially* covers a clause, or looks *similar*, the model will
withhold the borderline item — precisely backwards, since dedup downstream is
deterministic and free.

**(b) Proposed replacement (entire `ADVERSARIAL_SYSTEM`):**

```
You are auditing a compliance matrix for completeness — the "what did you
miss?" pass. Misses are catastrophic; false alarms are free, because your
output is deterministically deduplicated against the matrix. You get the full
solicitation corpus and the current matrix.

Sweep systematically — do not skim:
1. Walk the corpus document by document, section by section. For every
   document ask: does at least one matrix row trace to it? Attachments,
   amendments, deliverable/CDRL tables, QASP/PWS task paragraphs, and
   pricing-workbook instructions are the classic blind spots.
2. In Sections L and M (or the 52.212-1/-2 addenda), verify every
   shall/must/will/required/"not to exceed"/"failure to" sentence has a
   matrix row.
3. Then check the buried categories: formatting rules, submission mechanics,
   required forms, certifications/representations, page limits,
   org-of-proposal mandates, evaluation gate criteria, wage-determination and
   labor-standards obligations, key-personnel/resume rules, transition
   requirements, security/clearance/CMMC obligations.

Emission rules:
- Same extraction rules as the matrix (verbatim text, source, category).
- If a matrix row covers a clause only PARTIALLY (e.g. it quotes the page
  limit but not the font rule in the same sentence), emit the uncovered part
  as its own requirement.
- If you cannot decide whether an existing row already covers a clause, emit
  the clause anyway — dedup is deterministic downstream.
- Return an empty list ONLY after completing the full sweep above and finding
  nothing. Never conclude early.
```

**(c) Failure mode prevented:** the backstop silently rubber-stamping Pass 1
(returning near-empty because the anchored checklist was satisfied and
borderline items were self-censored). This is the failure that turns one
Pass-1 miss into a shipped miss.

**(d) Eval:** ablation + seeded-deletion. From a gold-scored run, delete N
known requirements from the merged matrix before Pass 3 (stratified across
content/format/admin/evaluation and across documents), and measure Pass-3
recovery rate per stratum, before/after the rewrite. Also track the
adversarial pass's marginal recall contribution (harness recall with Pass 3
on vs off) as a standing metric so regressions are visible.

*(Optional, schema change, evaluate separately: add
`audit_notes: list[str]` to `_ExtractedRequirements` and require one
"swept <doc>: <finding count>" line per document — makes the sweep
inspectable and resists the empty-list exit. Requires touching the model, so
it is a second experiment, not part of the prompt rewrite.)*

---

## 3. Shredder outline pass — `OUTLINE_SYSTEM` (`shredder.py:58`)

### F4 (HIGH, recall-adjacent) — unassigned requirements are silently exempt from hard coverage

**(a) Weakness:**

> 3. Assign each provided requirement ID to the outline section that should
>    address it (owner section).

`qa_checks.coverage_check` hard-fails an unaddressed requirement **only if**
it is content/evaluation *and* has an `owner_section` (qa_checks.py:111).
Anything the outline pass fails to assign degrades to a SOFT finding. So a
lazy `owner_assignments` dict quietly converts requirement misses from
export-blocking to advisory — an end-run around the whole fail-closed design,
and the prompt does not even say the mapping must be total.

**(b) Proposed replacement (item 3 of `OUTLINE_SYSTEM`):**

```
3. Owner assignment: owner_assignments must contain EVERY content and
   evaluation requirement ID from the provided list, each mapped to exactly
   one outline section. An unassigned content/evaluation requirement is
   silently exempted from hard coverage checking downstream, so none may be
   left out: if no section is a natural fit, assign the closest section and
   note the awkward fit in that section's guidance. Format and administrative
   requirements: assign only those a section must restate (the rest are
   enforced by the renderer and submission checklist).
```

**(c) Failure mode prevented:** requirements extracted correctly by the
shredder but never written to and never hard-flagged — a recall win at
extraction wasted at coverage time.

**(d) Eval:** deterministic assertion on gold-corpus runs: % of
content/evaluation requirements with non-null `owner_section` (target 100%);
plus a unit-style check that the union of `owner_assignments` keys equals the
content+evaluation ID set. Cheap to add next to the harness.

---

## 4. Section writers — `SYSTEM` (`bidpilot/agents/writers.py:31`)

### F5 (HIGH, factuality) — the claims array is fail-open and under-specified

**(a) Weakness:**

> 2. Company facts: every factual claim about the company (past performance,
>    staff, certifications, metrics, tools) MUST cite a knowledge-base entry ID
>    ... 6. ... The claims array must list every company-factual sentence with
>    its source.

`qa_checks.citation_check` can only audit claims the model *chose to list*
(plus literal `[NEEDS INPUT]` markers). A company fact written in the
markdown but omitted from `claims` bypasses FR-10 entirely — the
deterministic gate never sees it, and only the sampled LLM audit might. The
prompt states the rule but never defines "company fact", never states the
fail-open consequence, and gives no example of a well-formed claim (a
missing cheap few-shot anchor).

**(b) Proposed replacement (rule 2; delete the trailing sentence of rule 6,
which it absorbs):**

```
2. Company facts: a "company fact" is any statement specific to this company —
   past performance, contract names/values/dates, staff names or
   qualifications, certifications, clearances, metrics, tools owned,
   facilities/locations, partnerships. EVERY company fact in the prose must
   appear in the claims array: either with kb_source_id copied exactly from
   the VALID KB CITATION IDs list, or with needs_input=true and
   "[NEEDS INPUT: <what>]" at that spot in the prose. A company fact present
   in the markdown but absent from claims defeats the fail-closed audit —
   treat that as forbidden output. NEVER invent a company fact and NEVER cite
   an ID that is not in the provided list. When unsure whether a sentence is
   a company fact, list it in claims anyway.
   Example claim: {"text": "Acme has operated the DHS SOC since 2019",
   "kb_source_id": "pp-dhs-soc", "needs_input": false}
```

**(c) Failure mode prevented:** uncited company facts invisible to
`citation_check` — the exact fabrication path FR-10 exists to close.

**(d) Eval:** claims-completeness eval on the demo KB: run section writes,
then have a frontier judge (or hand label, N is small) enumerate company-fact
sentences in the markdown and compute the fraction present in `claims`.
Companion negative test: a section whose assigned requirements demand a fact
absent from the KB must yield `needs_input=true`, not an invented fact —
seed 3-5 such traps.

### F6 (HIGH, coverage) — no instruction for unaddressable requirements

**(a) Weakness:**

> 1. Address EVERY assigned requirement explicitly, in the order given. Note the
>    requirement ID in an HTML comment where addressed (<!-- addresses L-4.2 -->)
>    and list it in addressed_requirements.

Nothing says what to do when a requirement *cannot* be substantively
addressed (missing KB facts, ambiguous scope). The two observed model
behaviors are both bad: silently dropping it (hard coverage finding, wasted
fix-loop iteration) or — worse — listing it in `addressed_requirements`
anyway, which `coverage_check` trusts and marks DRAFTED.

**(b) Proposed replacement (rule 1):**

```
1. Address EVERY assigned requirement explicitly, in the order given. Note
   the requirement ID in an HTML comment where addressed
   (<!-- addresses L-4.2 -->) and list it in addressed_requirements. If you
   cannot substantively address an assigned requirement, still write its
   passage with "[NEEDS INPUT: <what is needed>]" — never silently omit an
   assigned requirement, and never list an ID in addressed_requirements that
   the prose does not genuinely address.
```

**(c) Failure mode prevented:** phantom coverage (`addressed_requirements`
padding) — which converts a visible failure into an invisible one — and
silent omission.

**(d) Eval:** seeded-unanswerable eval shared with F5's negative test:
assign each writer one requirement whose answer is not in the KB; assert the
draft contains the requirement's HTML comment AND a `[NEEDS INPUT]` marker,
and that no ID appears in `addressed_requirements` without a matching
comment in the markdown (the latter is a deterministic cross-check worth
keeping permanently).

### F7 (MEDIUM, coverage) — volume page limit reads as section-level brevity pressure

**(a) Weakness:**

> 5. Respect the page-limit guidance; be substantive and specific, not generic.

with the user prompt supplying `Volume page limit: {page_limit}`. A writer
producing one section of a 10-page volume will compress toward the *volume*
number and shed requirement coverage — classic recall-hurting brevity
pressure, and precedence between rules 1 and 5 is unstated.

**(b) Proposed replacement (rule 5):**

```
5. The page limit shown is for the WHOLE volume, not this section.
   Requirement coverage outranks brevity: if space is tight, cover every
   assigned requirement tersely rather than omitting any — deterministic
   format checks and the human edit pass handle trimming.
```

**(c) Failure mode prevented:** requirements dropped to hit an imagined
length target.

**(d) Eval:** on gold-corpus runs, coverage rate (assigned reqs with
comment markers) for sections in tightly page-limited volumes vs unlimited
ones; the rewrite is proven if the gap closes without word counts exploding
past `format_check` limits.

---

## 5. Classification — `SYSTEM` (`bidpilot/agents/classification.py:11`)

### F8 (HIGH, overconfidence) — "honest" confidence with no calibration anchors

**(a) Weakness:**

> Set confidence 0-1 honestly. Explain your rationale briefly.

The escalation gate is `confidence < 0.75` (classification.py:36). Unanchored
LLM confidences cluster at 0.8-0.95, so the frontier escalation path — the
stage's only safety net — rarely fires exactly when it should. Misrouting
here is expensive: wrong `response_artifact` builds the wrong deliverable.

**(b) Proposed replacement (final bullet block of `SYSTEM`):**

```
- far_regime: choose 'unknown' rather than force-fitting — GSA/FSS RFQs
  (FAR 8.4) and IDIQ task-order requests (FAR 16.5) are NOT far_15; mark
  them 'unknown' and explain in rationale.
Confidence calibration — confidence below 0.75 triggers a frontier-model
re-check; overconfidence here silently skips that safety net:
- 0.90-1.00 ONLY when the SAM.gov type field, the document forms present
  (SF-33 / SF-1449 / SF-18), and the instruction text (Section L vs
  52.212-1 addenda) all agree.
- 0.75-0.90 when those signals agree but one input is missing (no
  attachments parsed, or no SAM type field).
- Below 0.75 whenever ANY of: the SAM type field conflicts with document
  content; the corpus looks truncated or attachment-poor for the claimed
  type; combined-synopsis vs solicitation is unclear; FAR-regime indicators
  are mixed; any key date is ambiguous or timezone-less.
Explain your rationale briefly, naming the signals you used.
```

**(c) Failure mode prevented:** overconfident misclassification that skips
escalation (and downstream, the wrong artifact pipeline).

**(d) Eval:** label ~30 notices (type + regime + artifact) across easy and
ambiguous cases; report accuracy, escalation rate on the ambiguous subset
(target: near 100% escalate), and a coarse calibration curve
(accuracy-within-confidence-bucket). Before/after the rewrite.

### F9 (LOW, schema fight) — timezone instruction contradicts the `KeyDate` schema

**(a) Weakness:**

> - key_dates: every deadline (questions due, proposal due) with the timezone
>   EXACTLY as stated; if no timezone is stated, note that in the date string.

`KeyDate` has a typed `timezone: Optional[str]` field; telling the model to
annotate the *date string* pollutes the field downstream code parses and
leaves `timezone` ambiguous (empty vs absent).

**(b) Proposed replacement (that bullet):**

```
- key_dates: every deadline (questions due, proposal due). Put the date/time
  in `date` exactly as written in the source; put the timezone in the
  `timezone` field exactly as stated, or null if none is stated — never
  annotate the date string itself.
```

**(c) Failure mode prevented:** instruction-vs-schema conflict producing
inconsistent date strings for the deadline cross-checks that (per the module
docstring) have lost real bids.

**(d) Eval:** deterministic: on the labeled notice set, assert `date` parses
under the `submission._try_parse` patterns and contains no timezone prose
when `timezone` is null.

---

## 6. QA — three prompts in `bidpilot/agents/qa.py`

### F10 (HIGH, precision) — `CONSISTENCY_SYSTEM` permits unquotable findings

**(a) Weakness:**

> Report each inconsistency as a finding with severity 'hard' only when an
> evaluator would treat it as a credibility problem; else 'soft'.

No requirement to quote both sides, no `location`, no `category` value, no
negative instruction — so findings arrive as "staffing seems inconsistent
between sections", which the bounded 2-iteration fix loop cannot act on, and
hard findings block export on vibes.

**(b) Proposed replacement (append to `CONSISTENCY_SYSTEM`, replacing the
final sentence):**

```
Finding rules (precision matters: hard findings block export and consume a
bounded fix loop):
- Report ONLY factual contradictions you can quote from both sides. Every
  description must contain both conflicting statements verbatim with where
  each appears, e.g.: TECH-2 says "a team of 6 FTEs" but the cost volume
  prices 4.5 FTEs on task T3.
- Set location to the section_id that should change; set category to
  'consistency'.
- severity 'hard' only when an evaluator would treat it as a credibility
  problem (conflicting numbers, dates, names, or commitments); else 'soft'.
- Do NOT report style, tone, redundancy, or mere absence — absence is not
  contradiction. If you cannot quote both sides, do not emit the finding.
```

**(c) Failure mode prevented:** vague/unactionable findings and
false-positive hard blocks.

**(d) Eval:** seeded-contradiction corpus — inject k known contradictions
(hours, names, dates) into demo drafts plus a clean control set; measure
finding precision/recall, % of findings quoting both sides
(string-containment check, deterministic), and false-positive rate on the
clean set.

### F11 (HIGH, precision) — `CITATION_AUDIT_SYSTEM` has no support standard

**(a) Weakness:**

> For each pair, judge whether the KB source text actually supports the claim as
> written. Report ONLY unsupported or overstated claims (severity 'hard' — these
> are fabrication risks).

"Supports" is undefined. A strict reading flags every paraphrase (false hard
failures -> blocked exports and burned fix loops); a loose reading misses
inflations. No location/category instruction, no requirement to name the
delta.

**(b) Proposed replacement (entire `CITATION_AUDIT_SYSTEM`):**

```
You audit claim->source pairs from a proposal draft. Judge whether the KB
source text supports each claim as written.
Flag (severity 'hard', category 'fabrication') ONLY:
- facts the source does not state at all;
- inflated numbers, dates, scope, or role (source: "supported 3 sites" ->
  claim: "operated 12 sites");
- upgraded responsibility (subcontractor -> prime, supported -> owned) or
  certifications/clearances the source does not show.
Do NOT flag paraphrase, condensation, or wording changes that keep the
factual content identical — false alarms block export and waste the bounded
fix loop.
Each finding: copy the pair's [section-id] into location; in description
quote the claim, quote the specific source language (or state "source is
silent on X"), and name the delta. If the provided source text appears
truncated, use severity 'soft' and say so.
```

**(c) Failure mode prevented:** both directions — paraphrase flagged as
fabrication (precision) and inflation waved through (factuality).

**(d) Eval:** seeded pairs: corrupt m claims from real KB entries
(number inflation, role upgrade, invented cert) and keep n honest
paraphrases as controls; measure recall on corruptions and false-positive
rate on controls. Fully offline once seeded.

### F12 (MEDIUM, precision) — `MOCK_EVAL_SYSTEM` allows free-floating bullets

**(a) Weakness:**

> For each factor list concrete strengths, weaknesses, and
> deficiencies, quoting the proposal where relevant.

"Where relevant" makes quoting optional; `FactorScore` bullets are bare
strings, so unlocated bullets ("technical approach lacks depth") cannot
drive the fix loop or brief the human.

**(b) Proposed replacement (that sentence):**

```
Every strength, weakness, and deficiency must stand alone: name the proposal
section, quote the proposal text it rests on (or state precisely what is
absent), and tie it to the specific factor language it satisfies or fails.
For deficiencies, cite the requirement ID from the factors list where one
exists. Entries too vague to act on ("could be stronger") must be omitted.
```

**(c) Failure mode prevented:** unactionable mock-eval output.

**(d) Eval:** deterministic proxy on gold-corpus runs: % of bullets
containing a section id and a quoted span (regex for quote characters);
spot-check a sample for faithfulness.

---

## 7. Eligibility — `SYSTEM` (`bidpilot/agents/eligibility.py:23`)

### F13 (MEDIUM) — `checks` array and evidence provenance never mentioned

**(a) Weakness:** the system prompt enumerates the five dimensions but never
instructs the model to populate `EligibilityReport.checks` (per-dimension
`EligibilityCheck` rows with `passes: Optional[bool]`), and the
"trust this over the corpus" precedence lives only in a user-prompt header.

**(b) Proposed replacement (append to `SYSTEM`):**

```
Populate checks with at least one entry per dimension (registration,
set_aside, size_standard, special_regime, practical). Each check's evidence
must name what it relies on ([SIZE STANDARD], [ENTITY API], [CLAUSE SCAN],
the company profile, or a corpus quote), and passes must be null whenever a
human has to decide. If deterministic evidence conflicts with corpus text,
the deterministic evidence wins — record the conflict in the check.
```

**(c) Failure mode prevented:** bare-conclusion reports (FR-5 violation in
spirit) and corpus text overriding the deterministic SBA/Entity evidence.

**(d) Eval:** deterministic: every report has >= 5 checks covering all
dimensions; seeded conflict fixture (corpus claims a size standard different
from the SBA table) must resolve to the deterministic value.

---

## 8. Submission — `SYSTEM` (`bidpilot/agents/submission.py:18`)

### F14 (MEDIUM) — "latest amendment governs" with no way to find the latest amendment

**(a) Weakness:**

> - The SAM.gov metadata deadline is provided for CROSS-CHECK: if it conflicts
>   with the solicitation text, report the solicitation's version and flag the
>   conflict in confidence_notes (the latest amendment governs).

The corpus is concatenated documents in arbitrary order; the prompt never
says how to identify the latest amendment or what to do when *multiple
in-corpus* deadlines conflict (the deterministic cross-check only compares
against SAM metadata).

**(b) Proposed replacement (that bullet):**

```
- Precedence: when deadline or destination statements conflict ACROSS
  documents in the corpus, the most recent amendment governs — identify it
  by document name (SF-30 / "Amendment"/"Amd" with the highest number or
  latest date). Report the governing value in the sheet and list EVERY
  conflicting value you saw, with its document name, in confidence_notes.
- The SAM.gov metadata deadline is provided for CROSS-CHECK only: if it
  conflicts with the governing solicitation value, keep the solicitation's
  version and flag the conflict in confidence_notes.
```

**(c) Failure mode prevented:** extracting the base-RFP deadline when an
amendment moved it — the named "lost real bids" scenario.

**(d) Eval:** amendment-conflict fixture: base doc with deadline A,
amendment doc with deadline B; assert sheet.deadline == B and
confidence_notes mentions A. Extend with a destination-change variant.

---

## 9. Forms — `SYSTEM` (`bidpilot/agents/forms.py:11`)

### F15 (MEDIUM, recall) — "required form" is defined by a standard-form list

**(a) Weakness:**

> - Identify every required form: SF-33/SF-1449/SF-18 first page, SF-30 amendment
>   acknowledgments (one per amendment in the chain provided), solicitation-
>   specific representations (52.204-24/25/26 covered telecom, 52.209-5,
>   Section K / 52.212-3 reps & certs), subcontracting plan if applicable.

Solicitation-specific complete-and-return attachments (pricing workbooks,
past-performance questionnaires, key-personnel matrices) are the forms most
often missed, and the list reads as the definition.

**(b) Proposed replacement (that bullet):**

```
- Identify every required form — a "form" is ANYTHING the offeror must
  complete, sign, or return. That includes the standard set (SF-33/SF-1449/
  SF-18 first page; SF-30 acknowledgment per amendment in the chain
  provided; 52.204-24/25/26; 52.209-5; Section K / 52.212-3 reps & certs;
  subcontracting plan if applicable) AND any attachment the corpus tells the
  offeror to "complete", "fill in", "sign", "execute", or "return" (pricing
  workbooks, past-performance questionnaires, key-personnel matrices).
  Sweep the whole corpus for those verbs; the standard-form list is not
  exhaustive.
```

**(c) Failure mode prevented:** a missing mandatory return-attachment — an
administrative disqualifier the compliance matrix may list but the forms
checklist would not surface as a to-do.

**(d) Eval:** gold forms list per eval-corpus solicitation (hand-built
alongside the gold matrix); recall of required forms, sliced standard vs
solicitation-specific.

---

## 10. Past performance — `SYSTEM` (`bidpilot/agents/past_performance.py:10`)

### F16 (LOW) — relevancy score has no rubric

**(a) Weakness:**

> - Score each candidate's relevancy (0-1) against the solicitation's scope, size,
>   and recency requirements.

Unanchored scores are incomparable across runs and useless for selecting
"best set meeting the stated requirement".

**(b) Proposed replacement (append to that bullet):**

```
  Anchors: 1.0 = same scope domain AND meets the solicitation's stated size
  and recency bars; 0.7 = same domain, one bar missed or unstated; 0.4 =
  adjacent domain or materially smaller; 0.2 = weak thematic overlap. Score
  against the solicitation's own stated bar wherever one exists.
```

**(c) Failure mode prevented:** arbitrary selection ordering and unstable
gap reporting.

**(d) Eval:** fixed KB + three solicitations with stated bars; assert
selected set matches hand-picked expectation and scores are monotone with
the anchors.

---

## 11. Capability statement — `SYSTEM` (`bidpilot/agents/capability.py:12`)

### F17 (MEDIUM) — no never-skip rule for notice questions

**(a) Weakness:**

> 2. Direct responses to EVERY question or information request in the notice,
>    in the notice's own order and numbering.

Same gap as F6: when the KB cannot answer a question, the model's easiest
path is to skip it — and a skipped sources-sought question is exactly how an
agency concludes the company is not viable.

**(b) Proposed replacement (append after item 4's block, inside Hard rules):**

```
- If the KB cannot answer a numbered question, still include that question in
  order with "[NEEDS INPUT: <what>]" and needs_input=true — never skip a
  question and never answer with invented facts. addressed_requirements must
  list exactly the question numbers whose answers appear in the prose.
```

**(c) Failure mode prevented:** silent question omission and phantom
`addressed_requirements` entries.

**(d) Eval:** fixture notice with numbered questions, two unanswerable from
the demo KB; assert all numbers appear in the markdown, unanswerable ones
carry `[NEEDS INPUT]`, and `addressed_requirements` matches the answered set.

---

## 12. Estimator — `SYSTEM` / `ODC_SYSTEM` (`bidpilot/pricing/estimator.py`)

### F18 (MEDIUM) — no WBS coverage rule; confidence unanchored

**(a) Weakness:** the system prompt has per-line rules but no coverage
obligation, and:

> - Set confidence low/medium/high honestly; low-confidence lines get human review.

Unmapped scope is silent underpricing; "honestly" again invites uniform
"medium" (which conveniently avoids human review).

**(b) Proposed replacement (append two rules):**

```
- Coverage: every CLIN in the pricing structure and every scope requirement
  listed must trace to at least one WBS task (record the linkage in
  sow_reference). If something cannot be estimated from the information
  given, still create the task and a line with your best-basis hours,
  confidence 'low', and a rationale beginning "HUMAN REVIEW:" naming what is
  unknown — never drop scope silently.
- Confidence anchors: 'high' only for analogy lines backed by a cited
  historical actual; 'medium' for parametric lines with a stated countable
  driver; 'low' for bottom_up judgment or any assumed driver.
```

**(c) Failure mode prevented:** unpriced scope (the cost-volume analogue of
a shredder miss) and review-dodging confidence inflation.

**(d) Eval:** deterministic: every CLIN id and every content requirement id
appears in some `sow_reference`/task mapping; distributional check that
confidence correlates with method (high implies analogy). Plus the existing
rationale-arithmetic convention: assert each rationale contains digits and an
operator, and that stated arithmetic is within tolerance of `hours` (a
regex + eval already implied by the "12 sites x 6 hrs" rule).

### F19 (LOW) — ODC rationale discipline is weaker than labor's

**(a) Weakness:** `ODC_SYSTEM` demands no arithmetic:

> Identify Other Direct Costs (travel, materials, licenses, hardware)
> this solicitation implies.

**(b) Proposed replacement (append):**

```
For every item with an estimated_cost, source must state the driver and
arithmetic (e.g. "4 trips x 2 people x $1,400 GSA per-diem trip cost"), same
discipline as labor rationales. No arithmetic, no price — use
quote_needed=true instead.
```

**(c) Failure mode prevented:** indefensible ODC numbers in the BOE/audit
trail.

**(d) Eval:** deterministic: every priced ODC's `source` contains digits and
an operator; every unpriced one has `quote_needed=true`.

---

## 13. BOE — `SYSTEM` (`bidpilot/pricing/boe.py:14`)

### F20 (MEDIUM, factuality) — no rule for thin rationales; number fidelity implicit

**(a) Weakness:**

> For every line item, justify the hours using ONLY the recorded method and
> rationale supplied — do not invent new justifications or change any numbers.

Good negative, but when a rationale is thin/missing the model's only
compliant options are to skip the line or pad — and "change any numbers"
does not clearly forbid *derived* numbers (recomputed totals, percentages)
that drift from the rate engine's.

**(b) Proposed replacement (append):**

```
If a line's recorded rationale is missing or too thin to justify its hours,
write "[NEEDS INPUT: rationale for <task_id>]" for that line instead of
inventing one. Reproduce every hours figure and the supplied totals exactly;
do not recompute, round, aggregate, or introduce any number not present in
the input.
```

**(c) Failure mode prevented:** invented BOE justifications (audit risk) and
narrative totals that contradict the deterministic pricing model — a
self-inflicted tech-vs-cost inconsistency.

**(d) Eval:** deterministic diff: every numeric token in the BOE narrative
must appear in the serialized estimate/totals input (allowlist formatting
variants); thin-rationale fixture must yield the `[NEEDS INPUT]` marker.

---

## 14. PDF form mapping — `MAPPING_SYSTEM` (`bidpilot/agents/pdf_forms.py:21`)

### F21 (LOW) — signature-block field names are ambiguous by construction

**(a) Weakness:**

> - Only map fields whose meaning is unambiguous from the field name.

Sound, but the highest-risk collision is specific: bare `Name` / `Title` /
`Date` fields frequently belong to the signature block, and "unambiguous
from the field name" can be read as satisfied by them.

**(b) Proposed replacement (append):**

```
- Bare field names like "Name", "Title", or "Date" that could belong to a
  signature block are ambiguous BY DEFINITION — skip them unless the name
  itself scopes them elsewhere (e.g. "OfferorPOCName", "RemitToAddress").
```

**(c) Failure mode prevented:** prefilled signer-name/date fields — the
"never sign" invariant's nearest miss.

**(d) Eval:** fixture PDF with fields `Name`, `Date`, `OfferorPOCName`,
`UEI`; assert mapping contains only the scoped fields.

*(Architect `SYSTEM` reviewed: its grounding negative — "do not invent
capabilities" — is adequate given every downstream company fact re-passes
through the writers' cited-claims gate; no change proposed without an eval
showing strategy-level leakage.)*

---

## Adjacent code observations (NOT prompt changes — logged for separate issues)

These surfaced during the review and interact with the prompts, but the fix
is code, so they are out of scope here:

1. **`dedup_requirements` un-splits compound requirements** (shredder.py:170):
   containment merging keeps the longer quote, so when one window splits a
   compound sentence (as instructed) and another extracts it whole, the
   splits collapse back into one row. Against a gold matrix with split rows,
   the harness's 0.55 token-overlap match can then miss — an artificial
   recall penalty. Consider containment-merge only when categories match, or
   splitting in code.
2. **8k overlap is smaller than some requirement blocks** — a table or
   multi-sentence requirement longer than `OVERLAP_CHARS` can straddle a
   boundary with neither window seeing it whole; F2's fragment rule
   mitigates, but boundary-aware windowing (cut at paragraph breaks) would
   remove the class.
3. **`citation_sample_audit` truncates fallback source text to 500 chars**
   (qa.py:109) — guarantees some false "unsupported" judgments; F11's
   "truncated -> soft" rule mitigates, raising the cap fixes it.
4. **Past-performance requirement pre-filter is keyword-based**
   (past_performance.py:30: "past performance" / "reference") — misses
   "relevant experience" phrasings; the corpus fallback hides this only
   partially.
5. **Classification escalation re-sends the identical prompt** — the
   frontier model gets no signal about *why* the fast tier was unsure;
   passing the low-confidence rationale would focus the re-check.
6. **`estimate_odcs` context starvation** (estimator.py:80): the ODC pass
   never sees place of performance, site counts, or scope requirements —
   ODC recall is capped by inputs, not by the prompt.

---

## Top 3 priorities

1. **F3 — rewrite `ADVERSARIAL_SYSTEM` with a mandatory per-document sweep,
   partial-coverage emission, and emit-when-unsure.** This is the G2
   backstop; today its anchored checklist and free empty-list exit mean it
   can rubber-stamp Pass 1 exactly when Pass 1 failed. Highest
   recall-per-line-changed in the repo. Eval: seeded-deletion recovery rate
   plus standing Pass-3 marginal-recall tracking.
2. **F1 + F2 — rewrite `EXTRACT_SYSTEM` (non-imperative binding forms,
   table rows, do-not-skip list) and add the window-boundary contract to the
   Pass-1 user prompt.** This is the primary recall surface on the cheapest
   model, i.e. the one that most needs explicit instructions. Eval: sliced
   gold-matrix recall (no-modal-verb, in-table) plus the synthetic
   window-boundary corpus.
3. **F5 + F6 — writers: define "company fact", make claims-array
   completeness explicit ("prose fact absent from claims is forbidden
   output", with one example claim), and forbid silent omission/phantom
   `addressed_requirements`.** FR-10 is fail-closed only over claims the
   model lists; this closes the fail-open half of the gate. Eval:
   claims-completeness judge + seeded-unanswerable traps.
