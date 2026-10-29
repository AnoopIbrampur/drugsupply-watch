"""Label tests, including the point-in-time property: a label row for week t
must not change when information about weeks after t+h is added, and row
exclusion (in_shortage_now) must not change when anything after t is added."""

import pandas as pd

from rxradar.transform.labels import label_rows, week_coverage, week_grid
from rxradar.transform.shortage_episodes import build_episodes


def weekly_caps(n, start="2020-01-06"):
    return pd.Series(pd.date_range(start, periods=n, freq="7D"))


def make(listing_rows, caps):
    lst = pd.DataFrame(listing_rows, columns=["date", "name", "status"])
    lst["date"] = pd.to_datetime(lst["date"])
    lst["capture_ts"] = lst["date"].dt.strftime("%Y%m%d000000")
    filler = pd.DataFrame({"date": caps, "name": "Filler", "status": "Current"})
    filler["capture_ts"] = filler["date"].dt.strftime("%Y%m%d000000")
    lst = pd.concat([lst, filler], ignore_index=True)
    eps = build_episodes(lst[["capture_ts", "name", "status"]], key_fn=lambda n: n)
    cur = lst.loc[lst.status == "Current", ["date", "name"]].rename(
        columns={"date": "capture_date", "name": "family"}
    )
    weeks = week_grid(caps)
    rows = pd.DataFrame([(w, "F") for w in weeks], columns=["week", "family"])
    return label_rows(rows, week_coverage(weeks, caps), cur, eps).set_index("week")


def test_y90_is_positive_only_before_a_new_start():
    caps = weekly_caps(40)
    start = caps[20]
    out = make([(d, "F", "Current") for d in caps[20:25]], caps)
    assert out.loc[start - pd.Timedelta(days=7), "y90"]
    assert not out.loc[start - pd.Timedelta(days=7 * 14), "y90"]  # 98 days before
    assert out.loc[start - pd.Timedelta(days=28), "y30"]
    assert not out.loc[start - pd.Timedelta(days=35), "y30"]
    # During the episode the row is flagged for exclusion, not labelled.
    assert out.loc[start, "in_shortage_now"]
    assert not out.loc[start, "y90"]


def test_horizon_overlapping_a_capture_gap_is_unknown():
    caps = pd.concat([weekly_caps(10), weekly_caps(20, start="2020-07-06")], ignore_index=True)
    out = make([], caps)
    assert not out.loc[caps[5], "known90"]  # horizon spans the ~4-month gap
    assert out.loc[caps[12], "known30"]


def test_last_weeks_have_unknown_horizon():
    caps = weekly_caps(30)
    out = make([], caps)
    assert not out.iloc[-1]["known30"] and not out.iloc[-1]["known90"]


def test_labels_do_not_depend_on_data_after_the_horizon():
    caps = weekly_caps(60)
    early_ep = [(d, "F", "Current") for d in caps[20:24]]
    late_ep = [(d, "F", "Current") for d in caps[50:55]]  # starts > 90d after week 20
    a = make(early_ep, caps)
    b = make(early_ep + late_ep, caps)
    t = caps[10]
    cols = ["in_shortage_now", "y30", "y90", "known30", "known90"]
    assert a.loc[t, cols].tolist() == b.loc[t, cols].tolist()


def test_exclusion_uses_only_the_capture_at_t():
    """A one-week dropout inside an episode is merged into one episode, but the
    week where FDA's page didn't list the drug is not flagged in_shortage_now,
    because exclusion may not look ahead."""
    caps = weekly_caps(30)
    on = [5, 6, 7, 9, 10]  # absent at capture 8
    out = make([(caps[i], "F", "Current") for i in on], caps)
    assert out.loc[caps[7], "in_shortage_now"]
    assert not out.loc[caps[8], "in_shortage_now"]
    assert not out.loc[caps[8], "y90"]  # not a new start: merged flicker


def test_single_capture_blip_is_not_an_onset():
    caps = weekly_caps(40)
    out = make([(caps[20], "F", "Current")], caps)
    assert not out.loc[caps[19], "y90"]
