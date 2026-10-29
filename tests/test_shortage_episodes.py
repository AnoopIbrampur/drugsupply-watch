"""Episode construction is the label source, so its edge cases get tests."""

import pandas as pd

from rxradar.transform.shortage_episodes import build_episodes, normalize_name


def frame(rows):
    return pd.DataFrame(rows, columns=["capture_ts", "name", "status"])


def weekly(n, start="2020-01-06"):
    return [d.strftime("%Y%m%d000000") for d in pd.date_range(start, periods=n, freq="7D")]


def test_normalize_name_drops_brand_and_usp():
    assert normalize_name("Paliperidone (INVEGA) Extended Release Tablets") == (
        "paliperidone extended release tablets"
    )
    assert normalize_name("Digoxin Injection, USP") == "digoxin injection"


def test_new_episode_is_dated_at_first_current_capture():
    ts = weekly(6)
    rows = [(t, "Other Drug Tablet", "Current") for t in ts]
    rows += [(t, "Digoxin Injection", "Current") for t in ts[3:5]]
    rows += [(ts[5], "Digoxin Injection", "Resolved")]
    ep = build_episodes(frame(rows)).set_index("key")
    d = ep.loc["digoxin injection"]
    assert d.start == pd.Timestamp(ts[3][:8]) and not d.start_censored and not d.ongoing
    # Present in the very first capture -> start unknown (left-censored).
    assert ep.loc["other drug tablet"].start_censored


def test_short_absence_is_merged_long_absence_splits():
    ts = weekly(30)
    rows = [(ts[0], "Filler", "Current")]
    on = [1, 2, 4, 5] + [20, 21]  # 1-week flicker merged; 15-week gap splits
    rows += [(ts[i], "Drug A Injection", "Current") for i in on]
    rows += [(t, "Filler", "Current") for t in ts[1:]]
    ep = build_episodes(frame(rows))
    a = ep[ep.key == "drug a injection"]
    assert len(a) == 2
    assert list(a.start) == [pd.Timestamp(ts[1][:8]), pd.Timestamp(ts[20][:8])]


def test_start_after_capture_gap_is_censored():
    ts = weekly(3) + ["20210601000000"]  # ~5 month gap before the last capture
    rows = [(t, "Filler", "Current") for t in ts] + [(ts[-1], "New Drug Tablet", "Current")]
    ep = build_episodes(frame(rows)).set_index("key")
    assert ep.loc["new drug tablet"].start_censored
    assert ep.loc["new drug tablet"].ongoing


def test_no_future_information_used():
    """An episode's start must not change when later captures are added:
    labels for week t depend only on captures up to t (plus the horizon)."""
    ts = weekly(10)
    rows = [(t, "Filler", "Current") for t in ts]
    rows += [(t, "Drug B Capsule", "Current") for t in ts[4:7]]
    early = build_episodes(frame([r for r in rows if r[0] <= ts[6]]))
    full = build_episodes(frame(rows))
    s_early = early.set_index("key").loc["drug b capsule"].start
    s_full = full.set_index("key").loc["drug b capsule"].start
    assert s_early == s_full


def test_approx_family_key_bridges_2023_renaming():
    from rxradar.transform.shortage_episodes import approx_family_key

    pairs = [
        ("Amifostine Injection", "Amifostine Injection, Powder, Lyophilized, For Solution"),
        ("Amoxapine Tablets", "Amoxapine Tablet"),
        ("Alprostadil (Muse) Suppository", "Alprostadil Suppository"),
        ("Amoxicillin Oral Powder for Suspension", "Amoxicillin Powder, For Suspension"),
    ]
    for old, new in pairs:
        assert approx_family_key(old) == approx_family_key(new), (old, new)
    assert approx_family_key("Digoxin Injection") != approx_family_key("Digoxin Tablet")


def test_diluent_words_kept_when_they_are_the_drug():
    from rxradar.transform.shortage_episodes import approx_family_key

    assert approx_family_key("Dextrose Monohydrate 5% Injection").startswith("dextrose")
    assert "water" in approx_family_key("Sterile Water for Injection")


def test_capture_gap_does_not_split_a_continuous_episode():
    """Current right before and right after a 100-day hole with no captures is
    one episode: we never observed it absent."""
    ts = weekly(4) + weekly(4, start="2020-05-08")  # ~100 days without captures
    rows = [(t, "Drug C Injection", "Current") for t in ts]
    ep = build_episodes(frame(rows))
    assert len(ep) == 1
    assert ep.iloc[0].end == pd.Timestamp(ts[-1][:8])


def test_observed_absence_still_splits_after_capture_gap():
    ts = weekly(4) + weekly(16, start="2020-05-08")
    rows = [(t, "Filler", "Current") for t in ts]
    rows += [(t, "Drug D Tablet", "Current") for t in ts[:4] + ts[-2:]]  # 14 weeks seen absent
    ep = build_episodes(frame(rows))
    assert (ep.key == "drug d tablet").sum() == 2
