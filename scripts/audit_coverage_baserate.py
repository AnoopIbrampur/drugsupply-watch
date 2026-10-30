"""Phase 1 audit 5d + NADAC coverage.

- Base rate: new (uncensored) shortage episodes per 90-day window vs the number
  of candidate drug families, and what that implies for precision@k.
- NADAC coverage: share of shortage families (by NDC dosage form group) that
  have any NADAC price in the latest week.
"""

import sys

import pandas as pd

from rxradar.ingest.fda_registry import read_tsv
from rxradar.ingest.openfda import load_results
from rxradar.paths import derived_dir, latest_snapshot_dir
from rxradar.transform.shortage_episodes import approx_family_key, build_episodes, form_group

arch = latest_snapshot_dir("fda_shortage_archive")
lst = pd.read_parquet(derived_dir() / "listings.parquet")
ep = build_episodes(lst, merge_gap_days=56, key_fn=approx_family_key)
clean = ep[~ep.start_censored]
caps = pd.Series(sorted(pd.to_datetime(lst.capture_ts.str[:8].unique())))

# 90-day windows fully covered by captures (no gap > 21 days inside).
gap_ok = caps.diff().dt.days.fillna(0) <= 21
windows = pd.date_range("2015-01-01", "2026-07-01", freq="QS")
counts = []
for w in windows:
    inside = caps[(caps > w) & (caps <= w + pd.Timedelta(days=90))]
    covered = len(inside) >= 10 and gap_ok[inside.index].all()
    n = ((clean.start > w) & (clean.start <= w + pd.Timedelta(days=90))).sum()
    counts.append({"window": w.date(), "covered": covered, "new_starts": n})
cw = pd.DataFrame(counts)
cov = cw[cw.covered]
print(f"90d windows fully covered: {len(cov)}/{len(cw)}")
print(
    f"new starts per covered 90d window: mean {cov.new_starts.mean():.1f}, "
    f"median {cov.new_starts.median():.0f}, min {cov.new_starts.min()}, max {cov.new_starts.max()}"
)
print(cw.to_string(index=False))

ndc_dir = latest_snapshot_dir("fda_ndc_text")
prod = read_tsv(ndc_dir / "product.txt")
rx = prod[prod.PRODUCTTYPENAME == "HUMAN PRESCRIPTION DRUG"]
n_fam = rx.assign(
    f=rx.SUBSTANCENAME.str.upper()
    + "|"
    + rx.DOSAGEFORMNAME.str.split(",").str[0]
    + "|"
    + rx.ROUTENAME
).f.nunique()
per = cov.new_starts.mean()
print(f"\ncandidate families (current Rx, coarse form): {n_fam}")
print(f"base rate per family per 90d: {per / n_fam:.2%}")
for k in (25, 50, 100):
    print(
        f"k={k}: random precision@k ~{per / n_fam:.2%}; ceiling precision@k {min(1, per / k):.0%}"
    )

# Recurrence: how many uncensored starts are in a family that had an earlier episode?
first_start = ep.groupby("key").start.min()
rec = clean.apply(lambda r: r.start > first_start[r.key], axis=1).mean()
print(f"\nshare of uncensored starts that are recurrences of an earlier episode: {rec:.0%}")

# NADAC coverage by form group of shortage NDCs.
if len(sys.argv) > 1:
    nad = pd.read_parquet(sys.argv[1])
    nad_ndc11 = set(nad.ndc)
    sh = pd.json_normalize(load_results(latest_snapshot_dir("openfda_shortages")))

    def ndc11(p):  # 4-4-2 / 5-3-2 / 5-4-1 -> 5-4-2 without dashes
        a, b, c = p.split("-")
        return a.zfill(5) + b.zfill(4) + c.zfill(2)

    sh["ndc11"] = sh.package_ndc.map(ndc11)
    sh["form"] = sh.generic_name.map(form_group)
    sh["in_nadac"] = sh.ndc11.isin(nad_ndc11)
    fam = sh.groupby(["generic_name", "form"]).in_nadac.any().reset_index()
    print("\nNADAC coverage of current openFDA shortage families (any NDC priced):")
    print(fam.groupby("form").in_nadac.agg(["mean", "size"]).round(2).to_string())
    print(f"overall: {fam.in_nadac.mean():.0%} of {len(fam)} families")
