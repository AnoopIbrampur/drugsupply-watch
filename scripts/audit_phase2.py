"""Phase 2 report: family-level episodes, panel size, label positive rates."""

import pandas as pd

from rxradar.paths import derived_dir

ep = pd.read_parquet(derived_dir() / "family_episodes.parquet")
p = pd.read_parquet(derived_dir() / "panel.parquet")

dated = ep[~ep.start_censored]
print(
    f"family episodes: {len(ep)}; dated starts: {len(dated)} across {dated.key.nunique()} families"
)
print(f"censored: {ep.start_censored.sum()}")
print("dated starts per year:", dated.start.dt.year.value_counts().sort_index().to_dict())
d = (ep.end - ep.start).dt.days
print(f"duration days: median {d.median():.0f}, p90 {d.quantile(0.9):.0f}")
print("top start dates:", dated.start.dt.strftime("%Y-%m-%d").value_counts().head(5).to_dict())

print(f"\npanel rows: {len(p):,}; families: {p.family.nunique():,}; weeks: {p.week.nunique()}")
print("families per week (median):", int(p.groupby("week").family.nunique().median()))
for h in (90, 30):
    usable = p[p[f"known{h}"] & ~p.in_shortage_now]
    print(
        f"\nh={h}: usable rows {len(usable):,} ({len(usable) / len(p):.0%}), "
        f"positives {usable[f'y{h}'].sum():,}, rate {usable[f'y{h}'].mean():.3%}"
    )
    for name, mask in [
        ("injectable", usable.injectable),
        ("multisource_generic", usable.multisource_generic),
        ("generic or injectable", usable.injectable | usable.multisource_generic),
        ("neither", ~(usable.injectable | usable.multisource_generic)),
    ]:
        sub = usable[mask]
        print(
            f"  {name:22s} rows {len(sub):>9,}  pos {sub[f'y{h}'].sum():>6,}"
            f"  rate {sub[f'y{h}'].mean():.3%}"
            f"  share of positives {sub[f'y{h}'].sum() / max(1, usable[f'y{h}'].sum()):.0%}"
        )
    by_year = usable.groupby(usable.week.dt.year).agg(rows=(f"y{h}", "size"), pos=(f"y{h}", "sum"))
    print(
        "  usable rows / positive rows by year:",
        {y: (r.rows, r.pos) for y, r in by_year.iterrows()},
    )
