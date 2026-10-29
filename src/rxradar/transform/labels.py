"""Point-in-time labels for (family, week) rows (docs/decisions.md D4).

For week *t* (a Monday) and horizon *h* days:
- ``in_shortage_now``: the family is listed as Current in the latest capture
  on or before *t*. This decides row exclusion and uses nothing after *t*.
- ``y_h``: a dated (uncensored) episode for the family starts in (t, t+h].
  Episode starts come from ``build_episodes``. Its merge rule looks ahead,
  but a start date only says "the family was not Current for >= 8 observed
  weeks, then was": that is the event we predict, so using it in labels is
  correct, and it never feeds row selection or features.
  Single-capture episodes that end immediately are treated as glitches.
- ``known_h``: the horizon was fully observed. That means the capture before
  *t* is within 21 days, no gap between captures inside the horizon exceeds
  21 days, and captures continue past t+h. Rows with ``known_h`` False have no
  trustworthy label and are dropped from training and evaluation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAX_CAPTURE_GAP_DAYS = 21
HORIZONS = (30, 90)


def week_grid(captures: pd.Series) -> pd.DatetimeIndex:
    """Mondays from the first capture to the last."""
    first, last = captures.min(), captures.max()
    return pd.date_range(first + pd.offsets.Week(weekday=0), last, freq="W-MON")


def week_coverage(weeks: pd.DatetimeIndex, captures: pd.Series, horizons=HORIZONS) -> pd.DataFrame:
    """Per week: the as-of capture, whether status at t is known, and whether
    each horizon is fully observed. Family-independent."""
    caps = np.array(sorted(pd.to_datetime(captures.unique())), dtype="datetime64[ns]")
    w = weeks.values.astype("datetime64[ns]")
    idx = np.searchsorted(caps, w, side="right") - 1  # last capture <= t
    asof = caps[idx]
    out = pd.DataFrame({"week": weeks, "asof_capture": asof})
    out["status_known"] = (w - asof) <= np.timedelta64(MAX_CAPTURE_GAP_DAYS, "D")
    gaps = np.diff(caps).astype("timedelta64[D]").astype(int)
    for h in horizons:
        end = w + np.timedelta64(h, "D")
        j = np.searchsorted(caps, end, side="left")  # first capture >= t+h
        known = []
        for i0, j0, ok in zip(idx, j, out["status_known"], strict=True):
            if not ok or j0 >= len(caps):
                known.append(False)
            else:
                known.append(bool((gaps[i0:j0] <= MAX_CAPTURE_GAP_DAYS).all()))
        out[f"known{h}"] = known
    return out


def label_rows(
    rows: pd.DataFrame,
    coverage: pd.DataFrame,
    current: pd.DataFrame,
    episodes: pd.DataFrame,
    horizons=HORIZONS,
) -> pd.DataFrame:
    """Add labels to ``rows`` (columns: week, family).

    ``current``: (capture_date, family) pairs listed as Current.
    ``episodes``: output of ``build_episodes`` keyed by family.
    """
    # pandas 3 defaults to microsecond datetimes while numpy arrays here are
    # nanoseconds; joins require one unit, so normalize everything to ns.
    ns = "datetime64[ns]"
    rows = rows.astype({"week": ns})
    coverage = coverage.astype({"week": ns, "asof_capture": ns})
    current = current.astype({"capture_date": ns})
    episodes = episodes.astype({"start": ns, "end": ns})
    out = rows.merge(coverage, on="week", how="inner")
    flags = ["status_known"] + [f"known{h}" for h in horizons]
    out[flags] = out[flags].astype(bool)
    cur = current.rename(columns={"capture_date": "asof_capture"}).drop_duplicates()
    cur = cur.assign(in_shortage_now=True)
    out = out.merge(cur, on=["asof_capture", "family"], how="left")
    out["in_shortage_now"] = out["in_shortage_now"].fillna(False).astype(bool)

    # An episode seen in a single capture and then gone is a page glitch:
    # FDA's 2023-09-18 renaming briefly listed e.g. "Metronidazole Solution" for
    # one week. Real shortages last months (median ~550 days), so these are not
    # counted as onsets.
    blip = (episodes.start == episodes.end) & ~episodes.ongoing
    starts = episodes.loc[~episodes.start_censored & ~blip, ["key", "start"]]
    starts = starts.rename(columns={"key": "family", "start": "next_start"})
    starts = starts.astype({"family": out["family"].dtype}).sort_values("next_start")
    out = out.sort_values("week")
    out = pd.merge_asof(
        out,
        starts,
        left_on="week",
        right_on="next_start",
        by="family",
        direction="forward",
        allow_exact_matches=False,  # a start on day t is already public at t
    )
    days = (out["next_start"] - out["week"]).dt.days
    for h in horizons:
        out[f"y{h}"] = (days <= h).fillna(False).astype(bool)
    return out.drop(columns=["next_start"]).sort_values(["week", "family"]).reset_index(drop=True)
