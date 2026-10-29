"""Phase 1 audit 5a/5d: shortage episodes per year from archived FDA lists."""

import pandas as pd

from rxradar.ingest.fda_shortage_archive import load_listings
from rxradar.paths import latest_snapshot_dir
from rxradar.transform.shortage_episodes import approx_family_key, build_episodes, normalize_name

lst = pd.DataFrame(load_listings(latest_snapshot_dir("fda_shortage_archive")))
lst.to_parquet(latest_snapshot_dir("fda_shortage_archive") / "_listings.parquet")
dates = pd.to_datetime(lst.capture_ts.str[:8])
print(
    f"captures parsed: {lst.capture_ts.nunique()}  ({dates.min().date()} .. {dates.max().date()})"
)
per = lst.groupby("capture_ts").size()
print(f"rows per capture: min {per.min()} median {per.median():.0f} max {per.max()}")
print("captures with <20 rows (parse failures?):", list(per[per < 20].index))
cur = lst[lst.status == "Current"].groupby("capture_ts").name.nunique()
print("Current listings per capture, by year (median):")
print(cur.groupby(cur.index.str[:4]).median().to_dict())
print("distinct listing names ever:", lst.name.nunique())

print("distinct normalized names:", lst.name.map(normalize_name).nunique())
print("distinct approx family keys:", lst.name.map(approx_family_key).nunique())
for gap in (28, 56, 182):
    ep = build_episodes(lst, merge_gap_days=gap, key_fn=approx_family_key)
    clean = ep[~ep.start_censored]
    by_year = clean.groupby(clean.start.dt.year).size()
    print(f"\nmerge gap {gap}d: episodes {len(ep)}, uncensored starts {len(clean)}")
    print("  new (uncensored) starts per year:", by_year.to_dict())
    if gap == 56:
        ep.to_parquet(latest_snapshot_dir("fda_shortage_archive") / "_episodes_56d.parquet")
        d = (ep.end - ep.start).dt.days
        print("  duration days: median", d.median(), "p90", d.quantile(0.9))
        print("  distinct keys with >=1 uncensored start:", clean.key.nunique())
        print("  example episodes:")
        print(clean.sample(6, random_state=1).to_string(index=False))
