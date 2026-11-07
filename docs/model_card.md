# Model card: DrugSupply Watch shortage-onset ranking (Phase 3 backtest)

*Backtest numbers refreshed 2026-11-04, after 44 reviewed name overrides corrected the labels (287 dated onsets). Every number below comes from `scripts/run_backtest.py`,
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
- **Size:** 800,229 test family-weeks with 2,089 positive rows, from 186 onsets
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
| all families | random | 0.011 [0.000–0.027] | 0.032 [0.011–0.059] | 0.028 | 0.006 |
| | rule | 0.054 [0.022–0.086] | 0.075 [0.038–0.118] | 0.079 | 0.062 |
| | **logistic** | **0.129** [0.086–0.177] | **0.231** [0.172–0.290] | **0.219** | **0.130** |
| | LightGBM | 0.118 [0.070–0.167] | 0.204 [0.145–0.258] | 0.213 | 0.106 |
| generic or injectable | random | 0.019 | 0.056 | 0.071 | 0.022 |
| | rule | 0.056 | 0.087 | 0.084 | 0.066 |
| | **logistic** | **0.150** [0.094–0.206] | **0.269** [0.200–0.331] | **0.253** | **0.153** |
| | LightGBM | 0.138 [0.087–0.194] | 0.237 [0.175–0.300] | 0.247 | 0.117 |

**Reading:** a 100-family watchlist from the logistic model would have flagged
about **1 in 4** new shortages a month or more ahead, against about 1 in 30 for
random. It still misses three quarters of them.

### Row level and weekly precision (all families)

| model | PR-AUC | Brier | precision@25 | precision@50 | precision@100 |
|---|---|---|---|---|---|
| random | 0.0026 | 0.333 | 0.003 | 0.003 | 0.003 |
| rule | 0.0056 | n/a | 0.018 [0.008–0.028] | 0.009 | 0.006 |
| logistic | **0.0099** | 0.0027 | 0.020 | **0.020** [0.013–0.028] | **0.018** |
| LightGBM | 0.0086 | 0.0027 | **0.021** [0.012–0.032] | 0.018 | 0.016 |
| *ceiling* (all positives ranked first) | | | 0.311 | 0.155 | 0.078 |

- The base rate is 0.25%. The best models reach about 4× that in PR-AUC and
  about 7× random in precision@50.
- Precision@50 is still about 8× below its ceiling: **about 1 in 50 flagged
  family-weeks is followed by a new shortage within 90 days.**
- For context, the Berkeley project's ~0.1% precision (4-week horizon, unknown
  universe) is the same order as a random ranking at this base rate. This one
  is clearly above random but far from precise.

### By test year (recall@100 at a 4-week lead, all families; onsets evaluable)

| | 2017 | 2020 | 2021 | 2022 | 2023 | 2024 | 2026 |
|---|---|---|---|---|---|---|---|
| onsets | 33 | 40 | 15 | 37 | 35 | 6 | 5 |
| logistic | 0.15 | 0.23 | 0.47 | 0.11 | 0.37 | 0.17 | 0.20 |
| LightGBM | 0.15 | 0.15 | 0.47 | 0.16 | 0.20 | 0.33 | 0.40 |
| rule | 0.06 | 0.05 | 0.00 | 0.11 | 0.09 | 0.33 | 0.00 |

Year-to-year swings are large, and 2024 and 2026 have too few onsets to say
anything.

### Ablation (LightGBM, adding feature groups in turn)

| features | # | PR-AUC | precision@50 | recall@100, 4w |
|---|---|---|---|---|
| static only (form group, injectable, generic, # ingredients) | 4 | 0.0080 | 0.011 | 0.167 [0.118–0.220] |
| + shortage history | 14 | 0.0059 | 0.014 | 0.151 |
| + supply structure | 24 | 0.0067 | 0.017 | 0.167 |
| + recalls | 28 | 0.0070 | 0.017 | 0.177 |
| + prices (all) | 35 | 0.0086 | 0.018 | 0.204 [0.145–0.258] |
| all except history | 25 | 0.0072 | 0.016 | 0.167 |

**Honest reading:**
- Most of the measurable signal is *what kind of drug it is*: sterile
  injectable, multi-source generic, form.
- Supply, recall and price features nudge every metric up a little, and the
  full model is best on PR-AUC and recall. But the confidence intervals overlap
  at every step. **With ~190 evaluable onsets, this backtest cannot show that
  the supply-side signals help.** It can only say they don't hurt and point
  the right way.
- **LightGBM does not beat the 12-feature logistic regression.**

### Calibration (pooled test years)

| | mean predicted | observed | top 0.1% of scores: predicted / observed |
|---|---|---|---|
| logistic | 0.0036 | 0.0026 | 0.337 / 0.019 |
| LightGBM | 0.0024 | 0.0026 | 0.268 / 0.034 |

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

### Inspection data (Phase 3b): tested, not adopted

Adding FDA inspection and enforcement history did not improve the production model:
recall@100 at a 4-week lead went from 0.231 to 0.220, paired difference -0.011
[-0.043, +0.022]. The test was fixed before the run (decisions D11). What limits this test:
- Only 35% (2017) to 53% (2026) of families have a factory link. 130 of the 186 onsets did.
- 2,146 of 2,541 failed inspections have no dated classification letter, so they count
  only after a safety delay of 877 days, based on 362 dated results. Recent failures are
  invisible to the features, and this may be why they don't help.
- Labels came from companies still selling the drug today, so departed ones are missing.

So this says inspections didn't help as measured here, not that plant quality is
unrelated to shortages. Inspection counts appear on drug pages as facts and don't affect
the risk estimate.

## Intended use and limits

- The output is a ranked watchlist of supply risk. With precision around
  1–2%, **most flagged drugs will not go short**; the page has to say that
  plainly.
- It does not predict shortage severity or duration, and it gives no
  treatment guidance.
- Labels come from FDA's public list only. Shortages FDA never listed (e.g.
  ones only ASHP tracks) are invisible here, by design.
- 3.3% of names ever listed as Current don't map to a family, mostly
  multi-ingredient product classes (`docs/unmapped_listing_names.csv`). Their
  onsets are not in the labels.

## Production model and calibration (Phase 4)

**Choice:** the 12-feature logistic regression, not LightGBM.
- The two tie in the backtest.
- Logistic scores are a sum of per-feature terms, so the site's "why" reasons
  are exact contributions, not approximations.

**Calibration:** Platt scaling, chosen by a walk-forward check. Each test year
is calibrated only on earlier years' out-of-sample scores
(`model/calibrate.py`; 2020–2026, 701,607 rows):

| method | Brier | mean predicted | observed | top 0.1%: predicted / observed |
|---|---|---|---|---|
| raw | 0.00252 | 0.0036 | 0.0025 | 0.115 / 0.041 |
| **Platt** | **0.00251** | 0.0035 | 0.0025 | **0.026 / 0.019** |
| isotonic | 0.00251 | 0.0038 | 0.0025 | 0.028 / 0.000 |

Reliability for Platt, by predicted-probability bin:

| bin | predicted | observed |
|---|---|---|
| 0.5–1% | 0.66% | 0.66% |
| 1–2% | 1.34% | 1.82% |
| 2–5% | 2.34% | 3.01% |

- Brier is a near tie. Platt is well behaved across 0.5–5%, the range the
  watchlist shows, though it under-predicts somewhat above 1%. Isotonic's top
  0.1% claimed 2.8% and had no positives at all.
- After calibration the highest risks are only a few percent. So the site shows
  **bands relative to a typical family** (base rate 0.25% per 90 days):
  high ≥ 8×, elevated 4–8×, moderate 2–4×, typical < 2×.

**Caveat:** the production model trains on more data than any backtest model,
so calibration fitted on backtest scores is an approximation. It can be checked
again once a year of live scores exists.

**First live run (week of 2026-10-26):**
- 3,334 families ranked, plus 62 excluded as already in shortage.
- Bands: 4 high, 38 elevated, 148 moderate.
- The top is dominated by sterile injectables with past FDA shortages and
  recall-heavy manufacturers (#1: 0.9% sodium chloride injection, 7.2%).
- For many families the top reason is just "sterile injectable". That matches
  the ablation: drug type carries most of the signal.

## Plant data is not a model input

The plant map and simulator don't affect any risk estimate. A pre-registered test
(decisions D13) didn't show that few known plants goes with more shortages: rate ratio
1.31, 95% CI 0.90 to 1.97.
