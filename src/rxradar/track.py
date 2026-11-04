"""Live track record: freeze each week's predictions, then score them.

The rules are fixed in docs/decisions.md D9, written before any live result
existed. Backtests can flatter a model; these predictions were saved before
their outcomes were known, on a branch whose git history is the public
timestamp.

``freeze_week`` writes one immutable file per week. ``score_track`` compares
every frozen file with the shortage list as it stands today.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
from pathlib import Path

import pandas as pd

from rxradar.model.production import BANDS
from rxradar.paths import data_root, derived_dir

log = logging.getLogger(__name__)

SCHEMA = 1
KS = (50, 100)
MIN_LEAD_DAYS = 28
HORIZON_DAYS = 90
MAX_CAPTURE_GAP_DAYS = 21
MIN_ONSETS_FOR_BANDS = 30


def predictions_dir() -> Path:
    """The checkout of the ``predictions`` branch (in CI) or a local folder."""
    path = Path(os.environ.get("RXRADAR_PREDICTIONS", data_root() / "predictions"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def freeze_week(scores: dict, directory: Path, now: dt.datetime | None = None) -> Path | None:
    """Save this week's predictions unless the week is already frozen.

    Returns the new file, or None if the file already existed. A saved week is
    never rewritten, so a later retrain can't change what was predicted.
    """
    path = directory / f"{scores['week']}.json.gz"
    if path.exists():
        return None
    now = now or dt.datetime.now(dt.UTC)
    ranked = [e for e in scores["families"] if e["rank"] is not None]
    payload = {
        "schema": SCHEMA,
        "week": scores["week"],
        "frozen_at": now.isoformat(timespec="seconds"),
        "model": scores["model"],
        "freshness": scores["freshness"],
        "in_shortage": sorted(e["family"] for e in scores["families"] if e["in_shortage_now"]),
        "rows": [
            {
                "f": e["family"],
                "n": e["name"],
                "p": e["prob"],
                "b": e["band"],
                "r": e["rank"],
                "rr": e["rule_rank"],
            }
            for e in ranked
        ],
    }
    # mtime=0 keeps the bytes a pure function of the content.
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(json.dumps(payload, separators=(",", ":")).encode())
    log.info("froze week %s (%s families)", scores["week"], len(ranked))
    return path


def load_predictions(directory: Path) -> list[dict]:
    weeks = []
    for path in sorted(directory.glob("*.json.gz")):
        with gzip.open(path, "rb") as fh:
            p = json.loads(fh.read())
        p["anchor"] = pd.Timestamp(p["frozen_at"]).tz_localize(None).normalize()
        weeks.append(p)
    return sorted(weeks, key=lambda p: p["anchor"])


def week_status(anchor: pd.Timestamp, captures: pd.DatetimeIndex) -> str:
    """ "pending" until 90 days have been observed; "unknown" if a capture gap
    over 21 days falls in the window; otherwise "scored"."""
    end = anchor + pd.Timedelta(days=HORIZON_DAYS)
    if captures.max() < end:
        return "pending"
    before = captures[captures <= anchor]
    after = captures[captures >= end]
    edges = [before.max()] if len(before) else [anchor]
    window = list(captures[(captures > anchor) & (captures < end)])
    points = pd.DatetimeIndex(edges + window + [after.min()]).sort_values()
    gaps = points[1:] - points[:-1]
    return "unknown" if (gaps > pd.Timedelta(days=MAX_CAPTURE_GAP_DAYS)).any() else "scored"


def _score_weeks(weeks: list[dict], onsets: pd.DataFrame, captures) -> list[dict]:
    out = []
    for p in weeks:
        status = week_status(p["anchor"], captures)
        row = {"week": p["week"], "frozen": p["anchor"].date().isoformat(), "status": status}
        if status == "scored":
            end = p["anchor"] + pd.Timedelta(days=HORIZON_DAYS)
            hit = onsets[(onsets.start > p["anchor"]) & (onsets.start <= end)]
            positives = set(hit.family)
            n = len(p["rows"])
            row["random_precision"] = sum(r["f"] in positives for r in p["rows"]) / n if n else None
            for k in KS:
                top = [r for r in p["rows"] if r["r"] <= k]
                row[f"precision@{k}"] = sum(r["f"] in positives for r in top) / k
                rule = [r for r in p["rows"] if r["rr"] <= k]
                row[f"rule_precision@{k}"] = sum(r["f"] in positives for r in rule) / k
        out.append(row)
    return out


def _score_onsets(weeks: list[dict], onsets: pd.DataFrame) -> tuple[list[dict], int]:
    """Score each onset from the latest frozen week 28-90 days before it."""
    if not weeks:
        return [], 0
    first = weeks[0]["anchor"]
    scored, too_soon = [], 0
    for o in onsets.sort_values("start").itertuples():
        if o.start <= first:
            continue  # before tracking began
        lo, hi = (o.start - pd.Timedelta(days=d) for d in (HORIZON_DAYS, MIN_LEAD_DAYS))
        candidates = [p for p in weeks if lo <= p["anchor"] <= hi]
        if not candidates:
            too_soon += 1
            continue
        p = candidates[-1]
        if o.family in p["in_shortage"]:
            continue
        row = next((r for r in p["rows"] if r["f"] == o.family), None)
        rank = row["r"] if row else None
        rule_rank = row["rr"] if row else None
        rec = {
            "family": o.family,
            "name": row["n"] if row else o.family,
            "start": o.start.date().isoformat(),
            "scored_from": p["week"],
            "lead_days": (o.start - p["anchor"]).days,
            "rank": rank,
            "rule_rank": rule_rank,
            "prob": row["p"] if row else None,
            "n_ranked": len(p["rows"]),
        }
        for k in KS:
            rec[f"hit@{k}"] = rank is not None and rank <= k
            rec[f"rule_hit@{k}"] = rule_rank is not None and rule_rank <= k
        scored.append(rec)
    return scored, too_soon


def _band_table(weeks: list[dict], onsets: pd.DataFrame, statuses: list[dict]) -> list[dict]:
    done = {s["week"] for s in statuses if s["status"] == "scored"}
    totals = {b: {"n": 0, "pos": 0, "pred": 0.0} for _, b in BANDS}
    for p in weeks:
        if p["week"] not in done:
            continue
        end = p["anchor"] + pd.Timedelta(days=HORIZON_DAYS)
        hit = onsets[(onsets.start > p["anchor"]) & (onsets.start <= end)]
        positives = set(hit.family)
        for r in p["rows"]:
            t = totals[r["b"]]
            t["n"] += 1
            t["pos"] += r["f"] in positives
            t["pred"] += r["p"]
    return [
        {
            "band": b,
            "family_weeks": t["n"],
            "predicted": t["pred"] / t["n"] if t["n"] else None,
            "observed": t["pos"] / t["n"] if t["n"] else None,
        }
        for b, t in totals.items()
    ]


def score_track(weeks: list[dict], onsets: pd.DataFrame, captures: pd.DatetimeIndex) -> dict:
    """Score every frozen week and every onset since tracking began (D9)."""
    captures = pd.DatetimeIndex(captures).sort_values()
    status = _score_weeks(weeks, onsets, captures)
    scored, too_soon = _score_onsets(weeks, onsets)
    summary: dict = {}
    for k in KS:
        n = len(scored)
        summary[f"recall@{k}"] = sum(o[f"hit@{k}"] for o in scored) / n if n else None
        summary[f"rule_recall@{k}"] = sum(o[f"rule_hit@{k}"] for o in scored) / n if n else None
        summary[f"random_recall@{k}"] = (
            sum(min(1.0, k / o["n_ranked"]) for o in scored) / n if n else None
        )
        done = [s for s in status if s["status"] == "scored"]
        for key in (f"precision@{k}", f"rule_precision@{k}"):
            summary[key] = sum(s[key] for s in done) / len(done) if done else None
    done = [s for s in status if s["status"] == "scored"]
    summary["random_precision"] = (
        sum(s["random_precision"] or 0 for s in done) / len(done) if done else None
    )
    return {
        "started": weeks[0]["anchor"].date().isoformat() if weeks else None,
        "weeks_frozen": len(weeks),
        "weeks_pending": sum(s["status"] == "pending" for s in status),
        "weeks_scored": sum(s["status"] == "scored" for s in status),
        "weeks_unknown": sum(s["status"] == "unknown" for s in status),
        "onsets_scored": len(scored),
        "onsets_too_soon": too_soon,
        "summary": summary,
        "onsets": scored,
        "weeks": status,
        "bands": (
            _band_table(weeks, onsets, status) if len(scored) >= MIN_ONSETS_FOR_BANDS else None
        ),
    }


def load_outcomes() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Dated onsets and capture dates from the derived tables."""
    ep = pd.read_parquet(derived_dir() / "family_episodes.parquet")
    blip = (ep.start == ep.end) & ~ep.ongoing
    onsets = ep[~ep.start_censored & ~blip].rename(columns={"key": "family"})[["family", "start"]]
    lst = pd.read_parquet(derived_dir() / "listings.parquet", columns=["capture_ts"])
    captures = pd.DatetimeIndex(pd.to_datetime(lst.capture_ts.str[:8].unique(), format="%Y%m%d"))
    return onsets, captures


def update_track_record(scores: dict, out_dir: Path) -> dict:
    """Freeze this week if needed, rescore, and write track_record.json."""
    directory = predictions_dir()
    freeze_week(scores, directory)
    onsets, captures = load_outcomes()
    record = score_track(load_predictions(directory), onsets, captures)
    (out_dir / "track_record.json").write_text(json.dumps(record))
    return record
