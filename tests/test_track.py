"""Track record: frozen weeks are immutable and scoring follows D9."""

import datetime as dt
import gzip
import json

import pandas as pd

from rxradar import track

CAPS = pd.date_range("2027-01-04", "2027-08-02", freq="7D")


def scores(week="2027-01-04", prob=0.03):
    def entry(family, rank, rule_rank, short=False):
        return {
            "family": family,
            "name": family.title(),
            "prob": None if short else prob,
            "band": None if short else "high",
            "rank": rank,
            "rule_rank": rule_rank,
            "in_shortage_now": short,
        }

    return {
        "week": week,
        "model": {"trained_through": "2027-01-01", "sha256": "abc"},
        "freshness": {"FDA shortage list": week},
        "families": [
            entry("a", 1, 200),
            entry("b", 60, 2),
            entry("c", 300, 300),
            entry("short", None, None, short=True),
        ],
    }


def frozen(tmp_path, *weeks):
    for week, stamp in weeks:
        track.freeze_week(scores(week), tmp_path, dt.datetime.fromisoformat(stamp))
    return track.load_predictions(tmp_path)


def onsets(*rows):
    return pd.DataFrame(
        [(f, pd.Timestamp(s)) for f, s in rows], columns=["family", "start"]
    ).astype({"start": "datetime64[ns]"})


def test_a_frozen_week_is_never_rewritten(tmp_path):
    t = dt.datetime(2027, 1, 5, tzinfo=dt.UTC)
    first = track.freeze_week(scores(prob=0.03), tmp_path, t)
    original = first.read_bytes()
    later = t + dt.timedelta(days=2)
    assert track.freeze_week(scores(prob=0.99), tmp_path, later) is None
    assert first.read_bytes() == original
    assert json.loads(gzip.decompress(original))["rows"][0]["p"] == 0.03


def test_anchor_is_the_freeze_date_not_the_week_label(tmp_path):
    p = frozen(tmp_path, ("2027-01-04", "2027-01-08T10:00:00+00:00"))[0]
    assert p["anchor"] == pd.Timestamp("2027-01-08")


def test_onset_inside_the_window_is_a_hit_and_compares_with_baselines(tmp_path):
    weeks = frozen(tmp_path, ("2027-01-04", "2027-01-05T10:00:00+00:00"))
    out = track.score_track(weeks, onsets(("a", "2027-03-01"), ("b", "2027-03-01")), CAPS)
    assert out["onsets_scored"] == 2
    by = {o["family"]: o for o in out["onsets"]}
    assert by["a"]["hit@50"] and not by["a"]["rule_hit@50"]
    assert not by["b"]["hit@50"] and by["b"]["hit@100"] and by["b"]["rule_hit@50"]
    assert by["a"]["lead_days"] == 55
    assert out["summary"]["recall@50"] == 0.5
    assert out["summary"]["recall@100"] == 1.0
    assert out["summary"]["random_recall@50"] == 1.0  # k > 3 ranked families, so capped


def test_onset_closer_than_four_weeks_is_too_soon_not_a_miss(tmp_path):
    weeks = frozen(tmp_path, ("2027-01-04", "2027-01-05T10:00:00+00:00"))
    out = track.score_track(weeks, onsets(("a", "2027-01-20")), CAPS)
    assert out["onsets_scored"] == 0 and out["onsets_too_soon"] == 1


def test_onset_before_tracking_is_ignored(tmp_path):
    weeks = frozen(tmp_path, ("2027-01-04", "2027-01-05T10:00:00+00:00"))
    out = track.score_track(weeks, onsets(("a", "2026-12-01")), CAPS)
    assert out["onsets_scored"] == 0 and out["onsets_too_soon"] == 0


def test_family_already_in_shortage_is_skipped_and_unranked_family_is_a_miss(tmp_path):
    weeks = frozen(tmp_path, ("2027-01-04", "2027-01-05T10:00:00+00:00"))
    out = track.score_track(
        weeks, onsets(("short", "2027-03-01"), ("brandnew", "2027-03-01")), CAPS
    )
    assert [o["family"] for o in out["onsets"]] == ["brandnew"]
    assert not out["onsets"][0]["hit@100"]


def test_latest_eligible_week_is_used(tmp_path):
    weeks = frozen(
        tmp_path,
        ("2027-01-04", "2027-01-05T10:00:00+00:00"),  # 55 days before the onset
        ("2027-01-25", "2027-01-26T10:00:00+00:00"),  # 34 days: latest eligible
        ("2027-02-15", "2027-02-16T10:00:00+00:00"),  # 13 days: too close
    )
    out = track.score_track(weeks, onsets(("a", "2027-03-01")), CAPS)
    assert out["onsets"][0]["scored_from"] == "2027-01-25"
    assert out["onsets"][0]["lead_days"] == 34


def test_week_status():
    a = pd.Timestamp("2027-01-05")
    assert track.week_status(a, pd.date_range("2026-12-28", "2027-03-01", freq="7D")) == "pending"
    assert track.week_status(a, CAPS) == "scored"
    holes = pd.DatetimeIndex(["2027-01-04", "2027-02-20", "2027-04-20"])
    assert track.week_status(a, holes) == "unknown"


def test_pending_weeks_have_no_precision_and_empty_record_is_safe(tmp_path):
    empty = track.score_track([], onsets(), CAPS)
    assert empty["weeks_frozen"] == 0 and empty["summary"]["recall@50"] is None
    weeks = frozen(tmp_path, ("2027-07-26", "2027-07-27T10:00:00+00:00"))
    out = track.score_track(weeks, onsets(), CAPS)
    assert out["weeks_pending"] == 1 and out["summary"]["precision@50"] is None


def test_weekly_precision_counts_only_top_k_positives(tmp_path):
    weeks = frozen(tmp_path, ("2027-01-04", "2027-01-05T10:00:00+00:00"))
    out = track.score_track(weeks, onsets(("a", "2027-03-01")), CAPS)
    week = out["weeks"][0]
    assert week["status"] == "scored"
    assert week["precision@50"] == 1 / 50
    assert week["rule_precision@50"] == 0  # the rule ranked "a" 200th


def test_bands_stay_hidden_until_30_onsets(tmp_path):
    weeks = frozen(tmp_path, ("2027-01-04", "2027-01-05T10:00:00+00:00"))
    out = track.score_track(weeks, onsets(("a", "2027-03-01")), CAPS)
    assert out["bands"] is None
