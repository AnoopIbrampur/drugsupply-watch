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
from rxradar.model.production import (
    BANDS,
    BASE_RATE,
    ProductionModel,
    band,
    top_reasons,
    type_context,
)
from rxradar.paths import data_root, derived_dir
from rxradar.site.catalog import (
    alternatives,
    display_names,
    drug_group,
    form_label,
    lost_companies,
)

log = logging.getLogger(__name__)

HISTORY_WEEKS = 104
JUMP_PLACES = 25  # a rank rise this big inside the top JUMP_WITHIN counts as news
JUMP_WITHIN = 200
LOST_COMPANY_WINDOW_DAYS = 183
PEER_PERCENTILE = 90


def model_path():
    return derived_dir() / "model.json"


def inspection_facts(row: pd.Series) -> dict | None:
    """Plain counts for the drug page. None means no factory link: unknown,
    never a clean record. Not model inputs (decisions D11)."""
    n = row.get("insp_n_factories")
    if n is None or pd.isna(n):
        return None
    return {
        "factories": int(n),
        "inspected": int(row.insp_n_inspected),
        "failed_3y": int(row.insp_oai_3y),
        "actions_3y": int(row.insp_actions_3y),
    }


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
    groups = _family_groups(current_products, names, families)
    snapshots = sorted(ndc.published.unique())
    latest_ndc = ndc[ndc.published == snapshots[-1]]
    cutoff = snapshots[-1] - pd.Timedelta(days=LOST_COMPANY_WINDOW_DAYS)
    older = [d for d in snapshots if d <= cutoff]
    lost = lost_companies(ndc[ndc.published == older[-1]], latest_ndc) if older else {}
    peer_notes = _peer_notes(now)
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
                "context": type_context(row),
                "peer_note": peer_notes.get(fam),
                "group": groups.get(fam, "Other"),
                "n_generic_labelers": (
                    None if pd.isna(row.sup_n_generic_labelers) else int(row.sup_n_generic_labelers)
                ),
                "lost_companies": lost.get(fam, []),
                "inspections": inspection_facts(row),
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
    prior = prev_week.set_index("family")
    out = {
        "changes": _weekly_changes(entries, prior, lost),
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


def _family_groups(products: pd.DataFrame, names: pd.Series, families: list[str]) -> dict:
    classes = products.groupby("family").PHARM_CLASSES.agg(lambda c: ", ".join(sorted(set(c))))
    generic = products.groupby("family").NONPROPRIETARYNAME.agg(lambda c: " ".join(set(c)))
    return {
        f: drug_group(f, classes.get(f, ""), f"{names.get(f, '')} {generic.get(f, '')}")
        for f in families
    }


def _peer_notes(now: pd.DataFrame) -> dict[str, str]:
    """A note when a drug stands out among drugs of its own type: more recalls
    or more past shortages than at least 90% of its peers."""
    notes: dict[str, str] = {}
    measures = [
        ("rec_family_1y", "recalls in the past year"),
        ("hist_onsets_all", "past FDA-listed shortages"),
    ]
    for injectable, peers in now.groupby("injectable"):
        label = "injectable drugs" if injectable else "non-injectable drugs"
        for column, what in measures:
            pct = peers[column].rank(pct=True) * 100
            for fam in peers.index[(peers[column] > 0) & (pct >= PEER_PERCENTILE)]:
                # A percentile counts the drug itself, so 100 really means "the most".
                share = min(int(pct[fam]), 99)
                notes.setdefault(fam, f"More {what} than {share}% of {label}.")
    return notes


def _weekly_changes(entries: list[dict], prior: pd.DataFrame, lost: dict) -> dict:
    """What's new since last week, leaving out small moves around rank 50."""
    order = [b for _, b in BANDS]

    def summary(e: dict) -> dict:
        return {"name": e["name"], "slug": e["slug"], "form": e["form"]}

    new_short, resolved, up_band, jumps = [], [], [], []
    for e in entries:
        fam = e["family"]
        if fam not in prior.index:
            continue
        was = prior.loc[fam]
        if e["in_shortage_now"] and not was.in_shortage_now:
            new_short.append(summary(e))
        elif was.in_shortage_now and not e["in_shortage_now"]:
            resolved.append(summary(e))
        if e["rank"] is None or was.in_shortage_now:
            continue
        before = band(float(was.prob))
        if order.index(e["band"]) < order.index(before):
            up_band.append({**summary(e), "from": before, "to": e["band"], "rank": e["rank"]})
        elif (
            e["prev_rank"] is not None
            and e["rank"] <= JUMP_WITHIN
            and e["prev_rank"] - e["rank"] >= JUMP_PLACES
        ):
            jumps.append({**summary(e), "from": e["prev_rank"], "to": e["rank"]})
    return {
        "new_shortages": new_short,
        "resolved_shortages": resolved,
        "moved_up_band": sorted(up_band, key=lambda x: x["rank"]),
        "big_jumps": sorted(jumps, key=lambda x: x["to"]),
    }


def _current_products() -> pd.DataFrame:
    """Currently listed Rx products with their generic names (for display)."""
    p = pd.read_parquet(derived_dir() / "product_families.parquet")
    p = p[(p.listing == "current") & p.family.notna()]
    p = p.reindex(
        columns=["NONPROPRIETARYNAME", "PHARM_CLASSES", "family"]
    )  # older state lacks one
    return p.fillna({"NONPROPRIETARYNAME": "", "PHARM_CLASSES": ""})
