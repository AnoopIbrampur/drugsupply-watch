# Model card: RxRadar shortage-onset ranking (Phase 3 backtest)

*2026-10-29. Every number below comes from `scripts/run_backtest.py`,
`scripts/summarize_backtest.py` and `scripts/ablation.py` on the data
described in [data_audit.md](data_audit.md). Nothing is tuned on test years.*

**This describes supply risk, not clinical advice.**

## What it predicts

For each drug family (active moieties + coarse dosage form + route) and week
*t*: the probability that a **new** FDA-listed shortage starts in the next 90
days. Rows where the family is already in shortage at *t* are excluded
(decisions.md D4).

## Data and features (all point-in-time)

Each feature is computed only from data published by *t*. Every builder is
checked by `features/pit.py`, which recomputes the feature from data cut off
at *t* and fails on any difference.

| group | source | `published` date | examples |
|---|---|---|---|
| history (`hist_`, `mkt_`) | Wayback copies of FDA's shortage list | capture date | past onsets (3y / all), days since listed, discontinuation notices, sibling forms short now, market-wide shortages |
| supply (`sup_`) | 25 Wayback copies of FDA's NDC directory, 2016-06 → 2026-10 | capture date | labelers, generic labelers, products, top-labeler share, HHI, exits/entries since previous snapshot, snapshot age |
| recalls (`rec_`) | openFDA enforcement, NDC-matched | `report_date` | own recalls 90d / 1y, Class I, recalls by the family's labelers (1y) |
| prices (`px_`) | Medicaid NADAC 2013–2026 | `as_of_date` | 1y / 3y log price change, max weekly jump (13w), # surveyed NDCs |
| static | family key | n/a | form group, injectable, multi-source generic, # ingredients |

**Supply features read archived NDC snapshots, not today's files.**
Rebuilding past labeler counts from today's NDC files is unreliable
(`scripts/audit_ndc_backfill.py`). Compared with what FDA actually published:

| date | families published | families reconstructed | Spearman (labeler counts) | single-source flag agrees |
|---|---|---|---|---|
| 2016-06 | 4,051 | 2,655 | 0.59 | 61% |
| 2019-06 | 4,295 | 2,890 | 0.62 | 64% |
| 2022-04 | 3,049 | 3,091 | 0.81 | 82% |

Known feature limitations:
- **NDC snapshots are coarse.** They are 1–22 months apart, with no snapshot
  between 2022-04 and 2024-02. Between 2019-06 and 2019-12 the directory drops
  from 4,295 to 3,268 families, consistent with FDA's clean-out of uncertified
  listings, so `sup_exits` spikes there for administrative reasons.
- **Recalls map for only 30.5% of reports** (5,500 of 18,048). The rest are
  mostly compounding pharmacies (no NDC) and OTC products. Name-based matching
  was not used, to avoid false matches.
- **NADAC prices only 1,969 families**, and few injectables (retail survey).
- **No inspection or warning-letter features yet.** They need the FDA Data
  Dashboard key (Phase 3b).

## Backtest design

- **Walk-forward:** retrain each Jan 1, then score every week of that year.
  Test years are 2017, 2020, 2021, 2022, 2023, 2024 and 2026. 2018–19 and 2025
  are mostly capture gaps, and 2025 has no dated onsets.
- **Leakage guard:** training uses only rows whose 90-day label was resolved
  before the cutoff (*t* + 90 days < cutoff). This is tested in
  `tests/test_backtest.py`.
- **Size:** 795,137 test family-weeks with 1,988 positive rows, from 178 onsets
  evaluable at a 4-week lead.
- **Models:**
  - random;
  - a hand-written rule: recent own shortage > injectable > ≤2 labelers > sibling short;
  - L2 logistic regression on 12 transformed features;
  - LightGBM on 35 features, with fixed hyperparameters (`model/backtest.py`).

## Results (95% CIs: bootstrap over onsets, or over 13-week blocks of weeks)

### Headline: onset recall at a fixed lead

For each onset: was the family in that week's top-k at least 4 weeks before
it started? Random ≈ k / N.

| universe | model | recall@50, 4w | recall@100, 4w | recall@100, 8w | recall@50, 12w |
|---|---|---|---|---|---|
| all families | random | 0.017 [0.000–0.039] | 0.022 [0.006–0.045] | 0.064 | 0.007 |
| | rule | 0.090 [0.051–0.135] | 0.107 [0.062–0.152] | 0.105 | 0.085 |
| | **logistic** | **0.124** [0.073–0.169] | **0.242** [0.180–0.303] | **0.240** | **0.137** |
| | LightGBM | 0.107 [0.062–0.152] | 0.208 [0.152–0.264] | 0.193 | 0.105 |
| generic or injectable | random | 0.026 | 0.033 | 0.081 | 0.015 |
| | rule | 0.092 | 0.118 | 0.115 | 0.092 |
| | **logistic** | **0.144** [0.092–0.203] | **0.281** [0.209–0.353] | **0.284** | **0.160** |
| | LightGBM | 0.137 [0.085–0.190] | 0.242 [0.176–0.314] | 0.223 | 0.115 |

**Reading:** a 100-family watchlist from the logistic model would have flagged
about **1 in 4** new shortages a month or more ahead, against about 1 in 45 for
random. It still misses three quarters of them.

### Row level and weekly precision (all families)

| model | PR-AUC | Brier | precision@25 | precision@50 | precision@100 |
|---|---|---|---|---|---|
| random | 0.0025 | 0.333 | 0.003 | 0.003 | 0.003 |
| rule | 0.0058 | n/a | 0.020 [0.012–0.029] | 0.013 | 0.008 |
| logistic | **0.0099** | 0.0026 | 0.018 | **0.019** [0.013–0.026] | **0.018** |
| LightGBM | 0.0093 | 0.0026 | **0.023** [0.012–0.034] | 0.016 | 0.015 |
| *ceiling* (all positives ranked first) | | | 0.298 | 0.149 | 0.075 |

- The base rate is 0.25%. The best models reach about 4× that in PR-AUC and
  about 6× random in precision@50.
- Precision@50 is still about 8× below its ceiling: **roughly 1 in 55 flagged
  family-weeks is followed by a new shortage within 90 days.**
- For context, the Berkeley project's ~0.1% precision (4-week horizon, unknown
  universe) is the same order as a random ranking at this base rate. This one
  is clearly above random but far from precise.

### By test year (recall@100 at a 4-week lead, all families; onsets evaluable)

| | 2017 | 2020 | 2021 | 2022 | 2023 | 2024 | 2026 |
|---|---|---|---|---|---|---|---|
| onsets | 31 | 38 | 13 | 35 | 35 | 6 | 5 |
| logistic | 0.13 | 0.24 | 0.46 | 0.14 | 0.37 | 0.17 | 0.20 |
| LightGBM | 0.10 | 0.13 | 0.46 | 0.17 | 0.26 | 0.33 | 0.20 |
| rule | 0.16 | 0.08 | 0.00 | 0.14 | 0.11 | 0.50 | 0.00 |

Year-to-year swings are large, and 2024 and 2026 have too few onsets to say
anything.

### Ablation (LightGBM, adding feature groups in turn)

| features | # | PR-AUC | precision@50 | recall@100, 4w |
|---|---|---|---|---|
| static only (form group, injectable, generic, # ingredients) | 4 | 0.0077 | 0.010 | 0.152 [0.101–0.202] |
| + shortage history | 14 | 0.0071 | 0.014 | 0.163 |
| + supply structure | 24 | 0.0077 | 0.017 | 0.197 |
| + recalls | 28 | 0.0087 | 0.016 | 0.185 |
| + prices (all) | 35 | 0.0093 | 0.016 | 0.208 [0.152–0.264] |
| all except history | 25 | 0.0074 | 0.014 | 0.185 |

**Honest reading:**
- Most of the measurable signal is *what kind of drug it is*: sterile
  injectable, multi-source generic, form.
- Supply, recall and price features nudge every metric up a little, and the
  full model is best on PR-AUC and recall. But the confidence intervals overlap
  at every step. **With ~180 evaluable onsets, this backtest cannot show that
  the supply-side signals help.** It can only say they don't hurt and point
  the right way.
- **LightGBM does not beat the 12-feature logistic regression.**

### Calibration (pooled test years)

| | mean predicted | observed | top 0.1% of scores: predicted / observed |
|---|---|---|---|
| logistic | 0.0035 | 0.0025 | 0.296 / 0.019 |
| LightGBM | 0.0023 | 0.0025 | 0.232 / 0.067 |

- Both models are badly **overconfident at the top**. Raw scores must not be
  shown as probabilities.
- LightGBM's lower 60% of scores carry almost no ranking information: the
  observed rate is flat at ~0.17%.
- Recalibration (e.g. isotonic on a held-out later year) is Phase 4 work.

## Mistakes caught during Phase 3

1. **The first headline metric was biased toward random rankings.** "Top-k in
   any week before onset" let a random list beat every model (0.168 vs 0.130).
   It was replaced by fixed-lead recall (decisions.md D8 revised) *after*
   seeing that result. Both numbers are reported above.
2. **Today's NDC files were nearly used for past supply features.** The
   archive comparison above showed they misstate 2016–2019 badly.

## Intended use and limits

- The output is a ranked watchlist of supply risk. With precision around
  1–2%, **most flagged drugs will not go short**; the page has to say that
  plainly.
- It does not predict shortage severity or duration, and it gives no
  treatment guidance.
- Labels come from FDA's public list only. Shortages FDA never listed (e.g.
  ones only ASHP tracks) are invisible here, by design.
- 9.7% of names ever listed as Current don't map to a family, mostly
  multi-ingredient product classes. Their onsets are not in the labels.
