# RxRadar

A public early-warning system for US drug shortages. For each drug family
(active ingredient + dosage form + route), RxRadar will estimate the chance that
a new FDA-listed shortage starts in the next 90 days and explain why.

**Status: Phase 4 (site + nightly pipeline) built and tested locally; not yet
deployed.** Point-in-time features, baselines and a
walk-forward backtest: see [docs/model_card.md](docs/model_card.md). The best
model (a 12-feature logistic regression) would have put ~1 in 4 new shortages
in a 100-drug watchlist a month ahead (random: ~1 in 45), but only ~1 in 50
flagged family-weeks is followed by a shortage. `uv run rxradar site` builds the
static site into `public/`. Data
details: [docs/data_audit.md](docs/data_audit.md); decisions:
[docs/decisions.md](docs/decisions.md).

## Ground rules
- Only public US government data (openFDA, FDA, data.medicaid.gov), plus Internet
  Archive copies of FDA's own public shortage pages to rebuild history (see
  [docs/decisions.md](docs/decisions.md)).
- Point-in-time correctness: features for week *t* only use data public at week *t*.
- This describes supply risk. It is **not** clinical advice.
- Free and non-commercial.

## Quickstart
```bash
make install
make test
make ingest   # downloads raw snapshots into data/raw/
make build    # families, labels, weekly panel, features
uv run python scripts/run_backtest.py && uv run python scripts/summarize_backtest.py
```

All sources are free bulk downloads; no API keys are needed today. `make ingest`
takes hours on a first run, mostly the Medicaid NADAC history (~170 KB/s from
data.medicaid.gov). Re-runs skip immutable files. Run single sources with
`uv run rxradar ingest <openfda|substance|archive|ndc_archive|registry|warning_letters|nadac|dashboard>`.
`substance` (2.4 GB GSRS file) is reference data and not part of `all`.

## Deploying (GitHub Pages + nightly job)
The nightly workflow (`.github/workflows/nightly.yml`) runs from a compact state
archive (~100 MB of derived tables and captures) kept as a GitHub Release asset,
because the full raw history (~5 GB) doesn't fit a fresh runner. One-time setup,
after a full local `make ingest && make build` and a backtest run:

```bash
uv run rxradar train
uv run rxradar state pack state.tar.gz
gh release create state state.tar.gz --title "Pipeline state" --notes "Updated nightly by CI."
```

Then enable Pages (Settings → Pages → Source: GitHub Actions). Optional secrets:
`FDA_DD_USER`, `FDA_DD_KEY` (FDA Data Dashboard). Every night the job fetches FDA's
shortage page, openFDA and NDC files (and NADAC on Mondays), rebuilds, scores,
retrains on the 1st of each month, re-uploads the state and deploys `public/`.
