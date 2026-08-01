# The domain model

Code: `bidpilot/ml/corpus.py`, `requirements_model.py`, `train_requirements.py`,
`recall_net.py`. Train it with:

```bash
bidpilot model train                      # writes models/requirements.joblib
export BIDPILOT_REQ_MODEL=models/requirements.joblib
bidpilot model status                     # what it earned, and how it was measured
bidpilot model try "The offeror shall submit a subcontracting plan."
```

Training takes about 15 seconds on 4 CPUs and is deterministic, so the artifact
is not committed — regenerate it rather than trusting a pickle from a branch.

## What was asked, and what is actually true

The goal was to replace API calls with an in-house model trained to be an
expert on federal contracting. Half of that is done and running. The other half
is not possible on this machine, and it is worth being exact about which half is
which rather than shipping something that looks like a domain LLM and is not.

**A model that drafts proposals cannot be trained here.** Not "would be lower
quality" — cannot. Fine-tuning the smallest useful open-weight model needs base
weights, and `huggingface.co` is blocked by this environment's egress policy
(so is every other weight host: `api.openai.com`, `openrouter.ai`,
`registry.ollama.ai`). Training one from scratch instead means thousands of
GPU-hours on trillions of tokens; this container has four CPU cores, no GPU, and
15 GB of RAM. A small transformer trained from scratch on a domain corpus
produces fluent-looking text with no factual reliability, which for a document
that carries pricing, certifications, and binding commitments is worse than no
model at all.

**A model that reads solicitations expertly can be trained here, and was.**
That is the job this document describes.

## The split that makes this work

The backend does two very different things, and only one of them needs a large
general model:

| Job | Needs | Status |
|---|---|---|
| Read solicitation text and decide what binds you | Narrow domain expertise | **Trained, promoted, serving** |
| Draft prose, weigh strategy, red-team the result | Broad language + judgment | LLM (any endpoint — see README) |

The shredder is the highest-volume model stage in the pipeline: it reads the
*full* corpus on every run, in overlapping windows, because invariant 4 says
recall beats cost there. That is where domain expertise pays and where an
external dependency costs the most.

## What the model is

A supervised classifier over federal solicitation language. One sentence in;
out comes whether it is a binding requirement and, when the model has earned
that job, which of the four `RequirementCategory` kinds it is. TF-IDF over word
bigrams *and* character n-grams — solicitation language is distinguished as much
by morphology (`shall`, `12-point`, `not to exceed`) as by vocabulary, and
character n-grams survive the OCR damage this corpus routinely carries — into
calibrated logistic regression with balanced class weights.

Local, in-process, deterministic, free. No network.

## Measured results

Trained on 598 sentences across 78 template families. Every figure below is the
**worst of six family splits**, not one:

| Metric | Held-out (worst of 6) | Mean | Independent gold |
|---|---:|---:|---:|
| Requirement recall | **0.991** | 0.997 | 1.000 |
| Requirement precision | 0.851 | — | — |
| Category accuracy | 0.670 | 0.808 | 1.000 |
| Non-requirement text identified | 50% | — | — |

**Held-out families** are entire sub-topics the model never saw in training.
**Independent gold** is `evals/corpus_demo/gold_matrix.csv` — eight rows written
by hand for the eval harness, before this corpus existed and by a different
process.

The model is promoted **for screening only**. Category accuracy of 0.670 on the
worst split did not clear its 0.80 gate — note that the *mean* (0.808) would
have cleared it, which is precisely why the gate is on the worst split.
Categorization stays with the LLM.

`ships_screen` and `ships_categorize` are independent flags, each earned against
a gate declared before training. Passing one and failing the other is a narrower
job, not a lowered bar.

## Three things that make the numbers mean something

**The split is by family, never by row.** Templates within a family are
paraphrases of one another. A random split would put near-duplicates on both
sides and report a score the model has not earned. This is not hypothetical —
the first version of this model scored 0.256 category accuracy on held-out
families (chance is 0.25 for four classes) while looking fine on gold, because
holding out a whole family removed an entire sub-topic from training. The fix
was more domain coverage: 25 families became 53.

**The operating point is cross-validated, and conservative.** The serving rule
is not argmax — a sentence is dropped only when `P(none)` clears a threshold.
That threshold is chosen on train-side families across five folds, and the
**most conservative** fold wins. A single split once picked 0.35 and held-out
recall came in at 0.895, under the gate. The errors are wildly asymmetric — a
sentence wrongly kept costs the LLM one more line to read; a sentence wrongly
dropped is a requirement that never reaches the compliance matrix.

**The gate itself is measured across six splits, and reports the worst.** This
is not belt-and-braces; it changed the answer. Measured across seeds, this
corpus ranged **0.884–1.000** on recall and **0.698–0.871** on category
accuracy. An earlier version of this model was promoted for *both* jobs off a
single split — and would have shipped a categorizer that was right two times in
three on the unlucky seed.

**Evaluation uses the rule that actually serves.** Scoring with argmax would
measure a decision procedure the product does not use.

## The corpus, and its honest limits

Two sources, kept separate because they have different epistemic status:

- **Seed** — expert-authored templates over real solicitation phrasing. It is
  synthetic and labelled as such. It exists so the model has something to learn
  from before a single run has been captured: a cold start, not a substitute for
  real data.
- **Captured** — requirement text from real solicitations that real runs
  processed and a human let through a gate. Grouped by run so one solicitation
  never straddles the split. This is the corpus that matters, and it grows every
  run.

The seed set is the weak point and should be said plainly: a model trained on
language one author wrote will do best on language shaped like it. That is why
the independent gold check exists, why captured examples outrank seed ones, and
why the promotion gate is re-checked on every retrain. Point the trainer at your
runs to shift the balance:

```bash
python -m bidpilot.ml.train_requirements --runs runs --out models/requirements.joblib
```

## Two flavors of "format", and why that mattered

Format requirements come in two distinct flavors: **presentational mechanics**
(fonts, file types, naming, tabs, headers) and **extent limits** (page counts,
copy counts, word counts, file size). The first corpus covered only mechanics.
When the split held out the extent families, the model read page limits as
`content` or `evaluation` and scored 26/53 on format — because nothing in
training told it that "not to exceed 20 pages" was a format requirement at all.

Seven extent families later, format generalizes. This is the concrete shape
domain coverage takes: not more sentences, but more of the *concept*.

## How it is wired in

As a **recall net**, not a filter. After the shredder finishes, the model
re-reads the corpus and reports requirement-shaped sentences that do not appear
in the compliance matrix. They land in `HUMAN_ACTIONS.md` as candidates to
confirm or dismiss.

Nothing is auto-added: a false positive in the matrix is a requirement nobody
actually owes, which wastes real proposal pages. And nothing is removed — using
a 0.99-recall screen as a pre-filter would save tokens while silently
discarding roughly one requirement in a hundred. Invariant 4 points the other
way, and the arithmetic never favors trading recall for tokens on this stage.

The screening figure is therefore a measured property, not a live cost saving
by default.

### The cost lever, if you want it

`BIDPILOT_REQ_PREFILTER=1` runs the model in the other direction: sentences it
is confident bind nobody are dropped *before* windowing, so the LLM never reads
them. On a representative solicitation this cuts the shredded text by about
40%.

It is off by default and should stay off unless you have decided otherwise with
the number in front of you. At the promoted operating point, worst-split recall
is 0.991 — roughly **nine requirements in every thousand dropped before any
model sees them**, unrecoverable downstream because the text never arrives.
`bidpilot model status` prints that figure whenever the lever is on.

Two safeguards make the trade survivable rather than reckless: only confident
drops are made, at the same threshold the record was measured at; and dropped
text is sampled into the result so a reviewer can see what went.

## Serving discipline

Identical to the P(win) scorer:

- No promoted artifact → `load()` returns `None` and the pipeline behaves
  exactly as before. There is no fallback path to get wrong.
- A metrics sidecar must exist, say `ships_screen: true`, and carry a
  `model_sha256` matching the artifact's actual bytes. `joblib.load` is pickle,
  so a swapped artifact is arbitrary code execution, not merely a bad score.
- The trainer is the only thing that writes that flag, and only from held-out
  measurements.

## Getting to a real domain LLM

When you have a GPU and can reach weights, the pipeline for the drafting half
already exists — it is the MLE loop, and it has been waiting for data:

1. `BIDPILOT_CAPTURE_TRAINING_DATA=1` records every (system, prompt, output)
   triple beside the audit log.
2. `bidpilot mle collect` gathers them, including **preference pairs**: where a
   reviewer edited a section, the machine output and the human correction are
   both kept. Those are the highest-value examples in the system — they encode
   exactly what an expert fixes.
3. `bidpilot mle export` writes chat-format JSONL (train/val), with the
   human-corrected text as the assistant turn and the machine output alongside
   for DPO-style trainers.
4. Fine-tune off-box on that JSONL.
5. `bidpilot mle gate` scores the candidate against the Phase-0 eval harness and
   writes a promotion record. The recall gate is 0.98 — misses are catastrophic.
6. Serve it through `BIDPILOT_CUSTOM_LLM_URL`; no Anthropic key is involved.

The gap is data, not code. A useful fine-tune needs on the order of thousands of
reviewed examples, which means real runs with real reviewers — the loop in step
2 is how you accumulate them.

## Text as it actually arrives

Solicitations reach the model through PDF and DOCX extraction, which wraps
lines mid-sentence. Splitting on newlines alone turned one requirement into
several fragments — each too short to classify, and none quotable as verbatim
binding language. The splitter now rejoins wrapped continuations while still
respecting blank lines, bullets, numbered sub-paragraphs, and ALL-CAPS headings
as genuine block boundaries. Clause numbers (`52.222-43`) and abbreviations
(`Sec.`, `No.`) do not end sentences.

## Retraining

Retrain whenever captured examples grow materially, and read the record before
trusting it:

```bash
python -m bidpilot.ml.train_requirements --runs runs --out models/requirements.joblib
python -c "import json;print(json.load(open('models/requirements.metrics.json'))['held_out'])"
```

If a retrain fails its gate the artifact still gets written — and still refuses
to load. That is the design: a model that cannot prove it earned the job does
not get the job.
