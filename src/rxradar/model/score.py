"""Train the production model and score the latest week for the site.

``train()``: fit the logistic model on every row whose 90-day label is
resolved today, with Platt calibration from the backtest's out-of-sample
scores. Saves data/derived/model.json.

``score()``: score every family in the latest week whose status is known and
write data/site_data/scores.json:
- per family: calibrated probability, band, multiple of the base rate,
  top reasons, rank and last week's rank, and two years of weekly history;
- families currently on FDA's list (shown separately, never ranked);
- data freshness per source.
Everything uses the same point-in-time features as the backtest.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re

import numpy as np
import pandas as pd

from rxradar.model.backtest import rule_score
from rxradar.model.dataset import load_dataset
from rxradar.model.production import BASE_RATE, ProductionModel, band, top_reasons
from rxradar.paths import data_root, derived_dir
from rxradar.site.catalog import alternatives, display_names, form_label

log = logging.getLogger(__name__)

HISTORY_WEEKS = 104


def model_path():
    return derived_dir() / "model.json"


def site_data_dir():
    path = data_root() / "site_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slug(family: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return f"{base}-{hashlib.sha1(family.encode()).hexdigest()[:6]}"


def train(today: pd.Timestamp | None = None) -> ProductionModel:
    today = today or pd.Timestamp(dt.date.today())
    df = load_dataset()
    oos = pd.read_parquet(derived_dir() / "backtest_scores.parquet")
    oos = oos[oos.model == "logistic"]
    model = ProductionModel.fit(df, today, oos)
    model.save(model_path())
    log.info("trained through %s", model.trained_through)
    return model


def _freshness() -> dict:
    d = derived_dir()
    lst = pd.read_parquet(d / "listings.parquet", columns=["capture_ts"])
    ndc = pd.read_parquet(d / "ndc_snapshots.parquet", columns=["published"])
    px = pd.read_parquet(d / "prices_family_weekly.parquet", columns=["published"])
    rec = pd.read_parquet(d / "recalls_mapped.parquet", columns=["published"])
    return {
        "FDA shortage list": pd.Timestamp(lst.capture_ts.max()[:8]).date().isoformat(),
        "NDC directory": ndc.published.max().date().isoformat(),
        "NADAC prices": px.published.max().date().isoformat(),
        "openFDA recalls": rec.published.max().date().isoformat(),
    }


def score(model: ProductionModel | None = None) -> dict:
    model = model or ProductionModel.load(model_path())
    df = load_dataset()
    df = df[df.status_known]
    latest = df.week.max()
    recent = df[df.week > latest - pd.Timedelta(weeks=HISTORY_WEEKS)].copy()
    recent["prob"] = model.probability(recent)

    from rxradar.features.sources import load_listings

    listings = load_listings()
    current_products = _current_products()
    families = sorted(recent.family.unique())
    names = display_names(listings, current_products, families)
    ndc = pd.read_parquet(derived_dir() / "ndc_snapshots.parquet")
    alts = alternatives(ndc[ndc.published == ndc.published.max()], names)

    now = recent[recent.week == latest].set_index("family")
    contrib = model.contributions(now)
    ranked = now[~now.in_shortage_now].sort_values("prob", ascending=False)
    rank = pd.Series(np.arange(1, len(ranked) + 1), index=ranked.index)
    # The rule baseline's rank, for the track record (decisions D9). Ties break
    # by family key, not model order, so the baseline borrows nothing from the model.
    by_rule = ranked.assign(rule=rule_score(ranked), fam=ranked.index)
    by_rule = by_rule.sort_values(["rule", "fam"], ascending=[False, True])
    rule_rank = pd.Series(np.arange(1, len(by_rule) + 1), index=by_rule.index)
    prev_week = recent[recent.week == latest - pd.Timedelta(weeks=1)]
    prev = prev_week[~prev_week.in_shortage_now].sort_values("prob", ascending=False)
    prev_rank = pd.Series(np.arange(1, len(prev) + 1), index=prev.family.values)

    hist = recent.pivot_table(index="family", columns="week", values="prob")
    short_hist = recent.pivot_table(index="family", columns="week", values="in_shortage_now")
    entries = []
    for fam, row in now.iterrows():
        name = names.get(fam, fam)
        p = float(row.prob)
        entries.append(
            {
                "family": fam,
                "slug": slug(fam, name),
                "name": name,
                "form": form_label(fam),
                "in_shortage_now": bool(row.in_shortage_now),
                "prob": None if row.in_shortage_now else round(p, 5),
                "band": None if row.in_shortage_now else band(p),
                "multiple": None if row.in_shortage_now else round(p / BASE_RATE, 1),
                "rank": int(rank[fam]) if fam in rank else None,
                "prev_rank": int(prev_rank[fam]) if fam in prev_rank else None,
                "rule_rank": int(rule_rank[fam]) if fam in rule_rank else None,
                "reasons": [] if row.in_shortage_now else top_reasons(contrib.loc[fam], row),
                "n_labelers": None if pd.isna(row.sup_n_labelers) else int(row.sup_n_labelers),
                "injectable": bool(row.injectable),
                "alternatives": alts.get(fam, {"labelers": [], "other_forms": []}),
                "history": [
                    {
                        "week": w.date().isoformat(),
                        "prob": None if pd.isna(v) else round(float(v), 5),
                        "short": bool(short_hist.at[fam, w])
                        if pd.notna(short_hist.at[fam, w])
                        else False,
                    }
                    for w, v in hist.loc[fam].items()
                ],
            }
        )
    out = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "week": latest.date().isoformat(),
        "model": {
            "type": "calibrated logistic regression",
            "trained_through": model.trained_through,
            "sha256": hashlib.sha256(model_path().read_bytes()).hexdigest()[:16],
        },
        "base_rate": BASE_RATE,
        "freshness": _freshness(),
        "families": sorted(entries, key=lambda e: (e["rank"] is None, e["rank"] or 0, e["name"])),
    }
    (site_data_dir() / "scores.json").write_text(json.dumps(out))
    log.info("scored %s families for week %s", len(entries), out["week"])
    return out


def _current_products() -> pd.DataFrame:
    """Currently listed Rx products with their generic names (for display)."""
    p = pd.read_parquet(derived_dir() / "product_families.parquet")
    p = p[(p.listing == "current") & p.family.notna()]
    return p[["NONPROPRIETARYNAME", "family"]].fillna({"NONPROPRIETARYNAME": ""})
