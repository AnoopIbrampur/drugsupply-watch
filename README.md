# RxRadar

A public early-warning system for US drug shortages. For each drug family
(active ingredient + dosage form + route), RxRadar will estimate the chance that
a new FDA-listed shortage starts in the next 90 days and explain why.

**Status: Phase 1 (data de-risking).** No model exists yet. See
[docs/data_audit.md](docs/data_audit.md) for what the data can and can't support.

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
```

Set `OPENFDA_API_KEY` (free) in your environment for higher openFDA rate limits.
It is optional.
