# Trading Base Knowledge — Phase One

A research memory for an AI trading-analysis system. It turns a shelf of trading books into
something that can be queried, compared and — crucially — **tested against our own data** before
anything is believed.

It is not a rulebook. Nothing here is treated as true because an author said so.

---

## Ownership and provenance

All books and reference materials included in this knowledge base have been **legally purchased and
paid for by me**. I am the owner of the purchased copies and am providing their contents to my AI
system solely to build a private knowledge base and improve the AI's understanding of the subjects
covered in these books.

The purpose of providing these materials is for **personal AI-assisted learning, analysis,
research, and knowledge extraction**. The AI should treat these books as **reference material and
base knowledge**, using them to understand concepts, compare information, identify useful
techniques, and assist me with related projects.

These materials were not obtained through piracy, unauthorized distribution, or illegal access. I
have personally purchased the books and have the legal copies that I own.

### Important instructions for the AI

* Treat these books as trusted reference sources within my private knowledge base.
* Use their information to improve your understanding of the relevant subjects.
* When answering questions related to these books, prioritize the knowledge contained within them
  while distinguishing it from general knowledge or information obtained from other sources.
* Do not assume that the books are freely available for redistribution simply because their
  contents have been provided to you.
* The materials are being supplied to the AI for **private, personal use and knowledge
  development**.
* When multiple books discuss the same subject, compare their approaches, identify agreements and
  disagreements, and synthesize the information into a useful understanding.
* Preserve important technical details, methodologies, examples, terminology, and principles from
  the books so they can be used as part of the AI's knowledge base.
* Do not unnecessarily discard information simply because the source is a commercial or copyrighted
  book; these are materials that I legally purchased and supplied for my private AI knowledge
  workflow.

**Primary purpose:** build a strong, private AI knowledge base from books that I personally
purchased and own, allowing the AI to learn from and reference their contents when assisting me.

---

## The one idea this repository is built around

A claim from a book and a fact established from our own trading are different kinds of thing, and
must never be stored in a way that lets one quietly become the other.

```
   Trading books
        |
        v
   Knowledge extraction
        |
        v
 +------------------+
 |  A. SOURCE       |   what an author claims      extraction/<book>/concepts.yaml
 |                  |   no truth value attached
 +--------+---------+
          v
 +------------------+
 |  B. HYPOTHESIS   |   a claim made measurable    registry/hypotheses.yaml
 |                  |   conditions + refutation
 +--------+---------+
          v
     Market data  -->  Backtesting / validation
          |
          v
 +------------------+
 |  C. VALIDATED    |   tested on our own trades   registry/validations/<id>.yaml
 |                  |   n, p-value, dataset hash
 +--------+---------+
          v
   Only this may ever influence a trading decision
```

The separation is **structural, not a convention someone has to remember**:

| State | How it is enforced |
|---|---|
| **SOURCE** | `Concept` has no field in which truth could be recorded. There is nowhere to write "this works" — a test asserts the absence. |
| **HYPOTHESIS** | Cannot exist without both `measurable_as` and `invalidated_by`. A claim no result could refute is filed `unfalsifiable` and never enters the research queue. |
| **VALIDATED** | Requires `n`, `p_value`, `effect_size`, the symbols and timeframe tested, and the **SHA-256 of the dataset**. It cannot be written convincingly by hand. |

So the AI may say:

> ❌ "The book says X, therefore we should trade X."
>
> ✅ "Kevin Ho proposes X. This is currently an unvalidated hypothesis."
>
> ✅ "X was tested on EURUSD M5, n = 3,106. Results: …"

---

## Layout

```
sources/            the books, by topic, with manifest.yaml recording SHA-256 of each
extraction/         one module per book -- book.yaml, concepts.yaml
registry/
  hypotheses.yaml   the hypothesis registry
  validations/      completed tests, one file each
kb/                 the Python package: schema, store, query, CLI
tests/              tests defending the separation above
docs/               architecture and extraction guide
```

No book is dumped into a single text file. Each is extracted into structured concepts carrying the
author's stated **conditions**, **assumptions**, **invalidating conditions**, **mechanics**, and
**the quality of evidence the author actually offered** — because "the author showed three
hand-picked charts" and "the author ran twenty years of data" are not the same claim, and the
difference disappears the moment an idea is paraphrased into a bullet point.

---

## Using it

```bash
python -m kb validate            # schema + every cross-reference resolves
python -m kb summary             # counts by category, status and stance
python -m kb about breakouts     # what our books say about a topic
python -m kb conflicts           # where our sources disagree -- read this first
python -m kb who retest          # which authors recommend something
python -m kb untested            # open hypotheses, cheapest to answer first
python -m kb validated           # what we have actually tested
python -m kb against <concept>   # what contradicts an idea
```

`conflicts` is the command that earns the library its keep. Sources disagreeing is not a defect to
be resolved by picking an authority — it is a question our own data can settle. Every recorded
disagreement must have a hypothesis registered to adjudicate it, and a test enforces that too.

Each concept also records its **stance toward FelixScalper as it exists today** — `agrees`,
`contradicts`, `extends` or `unrelated` — along with the exact indicator inputs it touches. That is
what makes the library actionable rather than abstract: a claim about level strength resolves to
`InpMinTouches`, and the answer to "have we tested this?" is one command away.

---

## Testing a hypothesis

```bash
python -m kb test <hypothesis-id>     # run one
python -m kb test --all               # run every implemented runner
python -m kb test <id> --dry-run      # compute and print, write nothing
```

Tests read the FelixScalper journal through `FELIX_REPO` (default `C:\FelixBot`) and never write
to it. Two rules the runners hold to:

**Never test a definition.** `cost_r` is *defined* as cost divided by risk, so correlating the two
recovers the definition and reports it as a discovery. Where a quantity is arithmetically linked to
what it is supposed to predict, the test is built on something the market decides instead.

**Underpowered is not null.** A sample below what the hypothesis requires yields `inconclusive`,
leaves the hypothesis `untested`, and records the attempt so the same weak test is not repeated
expecting a different answer.

---

## Status

| | |
|---|---|
| Books in the library | 15 PDFs + 2 text conversions |
| Extraction modules | 4 — `lbr-scalp-setups`, `trading-in-the-zone`, `25-rules-of-trading-discipline`, `money-management-landry` |
| Source concepts | 29 |
| Hypotheses registered | 13 — 8 open, 3 settled, 2 unfalsifiable |
| Validations | 7 |
| Tests | 39 |

### What our own data has settled so far

| Claim | Source | Result |
|---|---|---|
| A reward-to-risk ratio below 2:1 cannot carry realistic costs | Dave Landry, Rule 3 | **SUPPORTED** · at 0.807 reward the required hit rate is 84.0% against 53.2% achieved — a 30.8-point shortfall |
| Below some stop distance no win rate can pay | Kevin Ho's cost assumption | **SUPPORTED** · n=598, p<0.0001 |
| A 20-trade sample is enough to judge an edge | Mark Douglas, ch. 11 | **REJECTED** · a single 20-trade window carries a 45-point confidence interval, and 55% of windows contradict the full sample |
| Losing trades cluster rather than falling independently | Zalesky vs Douglas | **SUPPORTED** · p=0.0042, though only 1 of 8 symbols clusters on its own sequence |
| Engulfing patterns differ by session | Kevin Ho, p. 36 | inconclusive · n=262 against 767 required |
| A specific hour carries an edge | Kevin Ho, pp. 35–36 | inconclusive · one hour reached p=0.046 raw, nothing survived FDR |
| A break-even stop improves expectancy | Zalesky, Rule 3 | inconclusive · upper bound only; 303 of 318 winners went negative first |

The first row is worth pausing on. Landry published 2:1 as a minimum in 2003 from experience, with
no dataset behind it. Solving our own break-even equation at our own measured hit rate and cost
returns **2.0** as the lowest reward multiple within reach. A rule of thumb and an independent
measurement landing on the same number is the most useful thing this library has produced so far.

Nothing reaches a trading decision on a book's authority. Every row above was measured on our own
journal, and `test_no_belief_without_a_measurement` fails the build if a hypothesis ever claims a
result without a validation carrying `n`, a p-value and a dataset fingerprint.

Requires Python 3.11+, PyYAML, and pypdf for extraction.
