"""Shortage-history features from archived FDA shortage lists.

Why: past shortages are the strongest known predictor of future ones (19% of
dated onsets are recurrences). A shortage in a sibling form of the same drug
(injection short, tablets next?) and overall market stress add context.

Source ``listings``: one row per (capture, listing name) with ``published``
(capture date), ``family`` (mapped, may be NaN) and ``status``.

Point-in-time notes:
- Episode *starts* depend only on earlier captures (an onset is a Current
  listing after >= 8 observed weeks absent), so counting starts <= t is
  safe. Episode *ends* need the future and are never used.
- The blip rule (single-capture episodes) needs the future too, so it isn't
  applied here: a feature at t can't know a listing will vanish next week.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rxradar.features.common import (
    NS,
    asof_capture,
    count_in_window,
    days_since_last,
    family_grid,
)
from rxradar.transform.shortage_episodes import build_episodes


def _moieties(family: str) -> list[str]:
    return family.split("|")[0].split("+")


def build(sources: dict[str, pd.DataFrame], weeks: pd.DatetimeIndex) -> pd.DataFrame:
    lst = sources["listings"]
    grid = family_grid(sources, weeks)
    if lst.empty or grid.empty:
        return grid
    captures = lst.published.unique()
    mapped = lst.dropna(subset=["family"])
    current = mapped.loc[mapped.status == "Current", ["published", "family"]].drop_duplicates()

    # Own history.
    grid["hist_days_since_listed"] = days_since_last(grid, current)
    grid["hist_current_captures"] = count_in_window(grid, current, None)
    # Episodes keyed by family directly; unmapped rows keep the capture
    # calendar intact (gap logic) but form no episodes.
    by_family = pd.DataFrame(
        {
            "capture_ts": lst.published.dt.strftime("%Y%m%d000000"),
            "name": lst.family,
            "status": lst.status,
        }
    )
    eps = build_episodes(by_family, key_fn=lambda f: f)
    episodes = eps.rename(columns={"key": "family", "start": "published"})
    # Starts first seen right after a capture gap (start_censored) are real
    # episodes but not dated onsets; counting them in windows would make
    # every gap look like a burst of new shortages.
    onsets = episodes.loc[~episodes.start_censored, ["family", "published"]]
    grid["hist_onsets_all"] = count_in_window(grid, episodes, None)
    grid["hist_onsets_3y"] = count_in_window(grid, onsets, 3 * 365)
    grid["hist_days_since_onset"] = days_since_last(grid, onsets)

    # Discontinuation notices: first time each listing name shows as
    # "To Be Discontinued".
    disc = mapped[mapped.status == "To Be Discontinued"]
    disc = disc.groupby("name").agg(published=("published", "min"), family=("family", "first"))
    grid["hist_disc_notices_1y"] = count_in_window(grid, disc, 365)

    # Sibling families (share an active moiety, different family).
    asof = asof_capture(grid.week, captures)
    cur_m = current.assign(moiety=current.family.map(_moieties)).explode("moiety")
    cur_m = cur_m.rename(columns={"family": "sib", "published": "asof"})
    g = grid[["week", "family"]].assign(asof=asof, row=np.arange(len(grid)))
    g = g.assign(moiety=g.family.map(_moieties)).explode("moiety")
    j = g.merge(cur_m, on=["asof", "moiety"])
    j = j[j.sib != j.family]
    grid["hist_siblings_short_now"] = (
        j.groupby("row").sib.nunique().reindex(range(len(grid)), fill_value=0).values
    )
    sib_onsets = onsets.assign(moiety=onsets.family.map(_moieties)).explode("moiety")
    sib_onsets = sib_onsets.rename(columns={"family": "sib"})
    gm = g.drop(columns="asof").merge(sib_onsets, on="moiety")
    gm = gm[(gm.sib != gm.family) & (gm.published <= gm.week)]
    gm = gm[gm.published > gm.week - pd.Timedelta(days=365)]
    grid["hist_sibling_onsets_1y"] = (
        gm.groupby("row").size().reindex(range(len(grid)), fill_value=0).values
    )

    # Market-wide stress at t.
    n_cur = current.groupby("published").family.nunique()
    grid["mkt_families_short_now"] = pd.Series(asof).map(n_cur).fillna(0).values
    w = pd.DataFrame({"week": grid.week, "family": "ALL"})
    grid["mkt_onsets_13w"] = count_in_window(w, onsets.assign(family="ALL"), 91)
    return grid


def listings_source(listings: pd.DataFrame, name_to_family: dict[str, str]) -> pd.DataFrame:
    """Turn the archive listings parquet into the ``listings`` source."""
    return pd.DataFrame(
        {
            "published": pd.to_datetime(listings.capture_ts.str[:8], format="%Y%m%d").astype(NS),
            "name": listings.name,
            "status": listings.status,
            "family": listings.name.map(name_to_family),
        }
    )
