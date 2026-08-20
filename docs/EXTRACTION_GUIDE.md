# Extraction guide

How to turn a book into an extraction module. Written so the next book is processed the same way
as the first, and so a concept written six months from now is still comparable with one written
today.

---

## 1. Place and hash the source

Put the file under `sources/<category>/`, then regenerate the manifest:

```bash
python -m kb validate      # after adding to sources/manifest.yaml
```

The manifest records a SHA-256 for every source file. An extraction module names the file it was
built from and repeats that hash in `book.yaml`. If the PDF is ever replaced, the mismatch surfaces
as a test failure rather than as citations that quietly point at the wrong pages.

---

## 2. Read the source properly

PDFs in this repository are text-extractable with `pypdf`:

```python
from pypdf import PdfReader
reader = PdfReader("sources/scalping/LBR_Scalp_setups.pdf")
print(reader.pages[0].extract_text())
```

Read the whole thing before writing a single concept. Partial reading produces concepts that miss
the author's stated conditions, and the conditions are usually the most valuable part.

**Check the metadata against the filename.** The first book processed here is filed as
`LBR_Scalp_setups.pdf`, which implies Linda Bradford Raschke. The article is by Kevin Ho; exactly
one of its six setups is hers. Attributing the other five to her would misquote two authors at
once. Filenames are not attribution.

---

## 3. Write `book.yaml`

```yaml
slug: lbr-scalp-setups
title: "Scalp Trading Methods from the Pit"
author: "Kevin Ho"
year: 2003
publication: "chartpoint magazine, pp. 34-37"
source_file: sources/scalping/LBR_Scalp_setups.pdf
source_sha256: 01ecb3b1...
pages: 4
categories: [scalping, breakouts, mean_reversion, momentum]
extraction_status: complete
notes: >
  Anything a reader must know before quoting this source -- misleading filename,
  the instrument and era it was written for, the kind of evidence offered.
```

The `notes` field is where the constraints on the whole book go. Use it. A reader who quotes a 2003
S&P-futures setup at a 2026 forex chart without knowing that is going to waste a month.

---

## 4. Write `concepts.yaml` — SOURCE knowledge

One entry per distinct claim. Not per chapter, not per page.

```yaml
- id: ho-15-minute-opening-range-scalp     # slug, no spaces, author-prefixed
  category: breakouts                      # from the 18 in kb/schema.py
  claim: >
    What the author proposes, in plain language, complete enough to act on.
  rationale: >
    Why the author says it should work.
  conditions:                              # what the author says is REQUIRED
    - "Only at the session open"
  assumptions:                             # what the author takes for granted
    - "A single, well-defined daily session open"
  invalidated_by:                          # what the author says breaks it
    - "Markets without a discrete session open"
  mechanics: [opening range, stop entry order]
  evidence_quality: anecdote                # none | anecdote | small_sample | large_sample | peer_reviewed
  author_claimed_result: "All 9 trades over 9 trading days were winners."
  conflicts_with: [ho-5-minute-standard-deviation-scalp]
  stance: contradicts                       # vs FelixScalper today
  felix_inputs: [InpBreakoutRetestMode]
  felix_current_behaviour: >
    What our system does instead, stated factually.
```

### Field discipline

**`claim`** — the author's proposition, never your paraphrase of whether it is good. There is no
field for that, deliberately.

**`conditions` vs `assumptions`.** Conditions are what the author says you must do. Assumptions are
what the author never questions — the instrument, the era, the liquidity, the cost structure.
Assumptions are where a setup silently fails when moved to a new market, so they are worth more
effort than they look.

**`evidence_quality`** — record what the author actually offered, not how persuasive it felt. Most
trading literature is `anecdote`. That is not an insult; it is the fact.

**`stance` and `felix_inputs`** — this is what makes the library actionable. Open
`MQL5/Indicators/FelixScalper.mq5`, find the input the claim would touch, and name it. If a claim
touches nothing in our system, `stance: unrelated` and an empty list is the honest answer.

**`conflicts_with`** — link disagreements across books, both directions. A test requires that every
recorded conflict has a hypothesis to settle it, so expect to write one.

---

## 5. Promote to `registry/hypotheses.yaml` — HYPOTHESIS

A concept becomes a hypothesis only when you can state **both**:

* `measurable_as` — the concrete comparison, in our own vocabulary (an input to flip, a grouping to
  run, a module to call)
* `invalidated_by` — the result that would make us abandon it

If you cannot write the second one, the entry is `unfalsifiable` with
`testability: not_measurable`. File it there and move on. Most psychology and discipline material
belongs in that bucket, and putting it there honestly is more useful than inventing a fake
measurement for it.

### `testability` — order the queue by cost, not by excitement

| Value | Meaning |
|---|---|
| `existing_data_full` | Answerable today from the journal as recorded |
| `existing_data_censored` | Partly answerable; tracking stopped before the answer was complete |
| `needs_new_data` | Requires running a changed configuration and collecting again |
| `needs_external_data` | Requires data the journal never captures |
| `not_measurable` | Cannot be expressed as a measurement |

The censored case is real and worth understanding. The journal records `atr`, `mfe` and `mae` in
price units, so different stop and target distances can be re-simulated against recorded
excursions. But tracking stops the moment a trade resolves — so **narrowing** a stop is fully
computable, while **widening** one is censored for every trade that was stopped out. Say which,
rather than implying a clean answer.

### `sample_required`

Trades needed to detect the claimed effect at 80% power, α = 0.05, from our 52.4% baseline:

| Effect claimed | Trades needed |
|---|---|
| +3 percentage points of win rate | 8,658 |
| +5 percentage points | 3,106 |
| +10 percentage points | 767 |

Record it. A hypothesis needing 8,658 trades is not wrong, but it should not sit at the top of a
queue above one answerable this afternoon.

---

## 6. Validation — the only way anything becomes believed

Do not hand-write a file in `registry/validations/`. It requires `n`, `p_value`, `effect_size`, the
symbols and timeframe tested, and the SHA-256 of the dataset — and the schema rejects anything
missing. That is the point: a validation is produced by running a measurement, not by being
convinced.

When one exists, set the hypothesis `status` to `supported` or `rejected` **and** fill
`validation_id`. Claiming a result without naming the validation is a schema error.

---

## 7. Verify

```bash
python -m kb validate
python -m pytest tests -q
```

Both must pass before committing. `validate` checks that every cross-reference resolves and that no
hypothesis claims a result it cannot back.
