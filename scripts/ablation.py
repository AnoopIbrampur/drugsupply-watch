"""Phase 3: which feature groups carry signal? LightGBM, adding groups in turn."""

import pandas as pd

from rxradar.model.backtest import run_lightgbm
from rxradar.model.dataset import feature_columns, load_dataset
from rxradar.model.metrics import summarize
from rxradar.paths import derived_dir

df = load_dataset()
allf = feature_columns(df)
base = ["injectable", "multisource_generic", "form_group", "n_moieties"]
groups = {p: [c for c in allf if c.startswith(p)] for p in ("hist_", "mkt_", "sup_", "rec_", "px_")}
steps = {
    "static only": base,
    "+history": base + groups["hist_"] + groups["mkt_"],
    "+supply": base + groups["hist_"] + groups["mkt_"] + groups["sup_"],
    "+recalls": base + groups["hist_"] + groups["mkt_"] + groups["sup_"] + groups["rec_"],
    "+prices (all)": allf,
    "all but history": [c for c in allf if not c.startswith(("hist_", "mkt_"))],
}
ep = pd.read_parquet(derived_dir() / "family_episodes.parquet")
onsets = ep[~ep.start_censored & ~((ep.start == ep.end) & ~ep.ongoing)]
onsets = onsets.rename(columns={"key": "family"})[["family", "start"]]
for name, feats in steps.items():
    s = run_lightgbm(df, feats)
    m = summarize(
        s, onsets[onsets.start.dt.year.isin(s.year.unique())], ks=(50, 100), lead_weeks=(4,)
    )
    print(
        f"{name:18s} n_feats={len(feats):2d}  pr_auc={m['pr_auc']:.4f}  "
        f"prec@50={m['prec@50'][0]:.3f} [{m['prec@50'][1]:.3f}-{m['prec@50'][2]:.3f}]  "
        f"recall@100_lead4w={m['recall@100_lead4w'][0]:.3f} "
        f"[{m['recall@100_lead4w'][1]:.3f}-{m['recall@100_lead4w'][2]:.3f}]",
        flush=True,
    )
