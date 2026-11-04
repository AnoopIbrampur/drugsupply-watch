# RxRadar

An early-warning watchlist for new US drug shortages, built only from public FDA and
Medicaid data. **[See the live site](https://anoopibrampur.github.io/rxradar/)**, updated
nightly, or read the **[case study](docs/case_study.md)**.

For each drug (same ingredient, form and route), RxRadar estimates the chance of a new
FDA-listed shortage in the next 90 days and explains why in plain words.

- **Backtest:** a 100-drug watchlist would have flagged about 1 in 4 new shortages at least
  a month early (random: about 1 in 30). Only about 1 in 50 flagged drug-weeks led to a
  shortage, so most flagged drugs won't go short.
- **Live track record:** each week's predictions are frozen publicly before outcomes are
  known, and scored by [rules set in advance](docs/decisions.md).
- **Runs at $0:** nightly GitHub Actions, GitHub Pages, free government data.

Details: [model card](docs/model_card.md), [data audit](docs/data_audit.md),
[decisions](docs/decisions.md).

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

## Track record
Each week's predictions are frozen before their outcomes are known, on the
[`predictions`](https://github.com/AnoopIbrampur/rxradar/tree/predictions) branch, and
scored by rules fixed in advance ([decisions D9](docs/decisions.md)). Saved weeks are
never rewritten, and results appear on the site's Track Record page.

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

Then enable Pages (Settings → Pages → Source: GitHub Actions).

FDA's `accessdata.fda.gov` pages sit behind bot protection that redirects cloud
runners to an "abuse detection" page, so the nightly job never requests FDA's
shortage page. It records openFDA's daily shortage export instead (same list),
and treats any bot-protection redirect as a hard stop, never a retry. Optional secrets:
`FDA_DD_USER`, `FDA_DD_KEY` (FDA Data Dashboard). Every night the job fetches FDA's
shortage page, openFDA and NDC files (and NADAC on Mondays), rebuilds, scores,
retrains on the 1st of each month, re-uploads the state and deploys `public/`.
