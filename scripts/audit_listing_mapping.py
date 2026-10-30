"""Phase 2: how many archived shortage-list names map to a drug family?"""

import pandas as pd

from rxradar.ingest.openfda import load_results
from rxradar.paths import derived_dir, latest_snapshot_dir
from rxradar.transform.families import MoietyResolver
from rxradar.transform.listing_names import ListingNameMapper, openfda_name_families
from rxradar.transform.substances import load_tables

prod = pd.read_parquet(derived_dir() / "product_families.parquet")
resolver = MoietyResolver(*load_tables(latest_snapshot_dir("openfda_substance")))
names = openfda_name_families(load_results(latest_snapshot_dir("openfda_shortages")), prod)
mapper = ListingNameMapper(resolver, prod.family.dropna().unique().tolist(), names)

lst = pd.read_parquet(derived_dir() / "listings.parquet")
cur = lst[lst.status == "Current"]
uniq = pd.DataFrame({"name": lst.name.unique()})
res = uniq.name.map(mapper.map)
uniq["family"], uniq["method"] = res.str[0], res.str[1]
uniq.to_parquet(derived_dir() / "listing_name_families.parquet")

print(f"distinct listing names: {len(uniq)}")
print(uniq.method.value_counts().to_string())
print(f"mapped: {uniq.family.notna().mean():.1%}")
cur_names = uniq[uniq.name.isin(cur.name)]
print(
    f"mapped among names ever listed as Current: {cur_names.family.notna().mean():.1%} "
    f"(n={len(cur_names)})"
)
w = cur.groupby("name").size().rename("weeks")
cw = cur_names.join(w, on="name")
print(
    f"mapped, weighted by Current listing-weeks: "
    f"{(cw.weeks * cw.family.notna()).sum() / cw.weeks.sum():.1%}"
)
for m in [
    "no_ingredient_found",
    "no_family_with_these_ingredients",
    "ambiguous_form",
    "form_not_found",
]:
    ex = uniq[uniq.method == m].name.head(8).tolist()
    print(f"\n{m}: {ex}")
