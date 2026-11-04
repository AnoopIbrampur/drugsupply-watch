"""Inspection features: D10 dating, label versions, unknown vs clean, no look-ahead."""

import numpy as np
import pandas as pd

from rxradar.features import inspections
from rxradar.features.pit import check_point_in_time

WEEKS = pd.date_range("2019-01-07", periods=160, freq="W-MON")
F = "X|INJECTION|PARENTERAL"
CLEAN = "Y|TABLET|ORAL"
UNLINKED = "Z|TABLET|ORAL"


def insp_row(code, end, fmd=None, area="Drug Quality Assurance", iid="1", fei="0001", country="IN"):
    return {
        "FEINumber": fei,
        "InspectionID": iid,
        "ClassificationCode": code,
        "InspectionEndDate": end,
        "FMD145Date": fmd,
        "ProjectArea": area,
        "CountryCode": country,
    }


def test_dating_follows_d10():
    rows = [
        insp_row("OAI", "2020-01-01", "2020-06-01T04:00:00Z", iid="1"),
        insp_row("OAI", "2020-01-01", iid="2"),
        insp_row("NAI", "2020-01-01", iid="3"),
        insp_row("OAI", "2020-01-01", area="Bioresearch Monitoring", iid="4"),
    ]
    df = inspections.inspection_source(rows).set_index("inspection_id")
    assert df.loc["1", "published"] == pd.Timestamp("2020-07-01")  # letter + 30
    assert df.loc["2", "published"] == pd.Timestamp("2020-01-01") + pd.Timedelta(days=877)
    assert df.loc["3", "published"] == pd.Timestamp("2020-01-01") + pd.Timedelta(days=197)
    assert "4" not in df.index  # clinical-trial inspections aren't plant quality
    assert df.loc["1", "fei"] == "1"  # leading zeros normalized


def test_action_is_dated_30_days_after_it_was_taken():
    rows = [{"FEINumber": "1", "ActionTakenDate": "2020-03-01", "ActionType": "Warning Letter"}]
    assert inspections.action_source(rows).published[0] == pd.Timestamp("2020-03-31")


def sources():
    t = pd.Timestamp
    factories = pd.DataFrame(
        [
            (F, "S1", 1, t("2018-01-01"), "1"),  # old version: factory 1 only
            (F, "S1", 2, t("2020-06-01"), "1"),  # new version adds factory 2
            (F, "S1", 2, t("2020-06-01"), "2"),
            (CLEAN, "S2", 1, t("2018-01-01"), "3"),
            (UNLINKED, "S3", 1, t("2018-01-01"), None),  # label lists no factory
        ],
        columns=["family", "set_id", "version", "published", "fei"],
    )
    insp = pd.DataFrame(
        {
            "published": pd.to_datetime(["2020-02-01", "2019-01-01"]),
            "fei": ["1", "3"],
            "code": ["OAI", "NAI"],
            "country": ["IN", "US"],
            "inspection_id": ["a", "b"],
        }
    )
    actions = pd.DataFrame(
        {"published": pd.to_datetime(["2020-09-01"]), "fei": ["2"], "action": ["Warning Letter"]}
    )
    return {"factories": factories, "inspections": insp, "actions": actions}


def built():
    return inspections.build(sources(), WEEKS).set_index(["week", "family"])


def test_failed_inspection_counts_only_after_publication():
    out = built()
    assert out.loc[(pd.Timestamp("2020-01-27"), F), "insp_oai_3y"] == 0
    assert out.loc[(pd.Timestamp("2020-02-03"), F), "insp_oai_3y"] == 1


def test_new_label_version_changes_the_factories_at_its_date():
    out = built()
    assert out.loc[(pd.Timestamp("2020-05-25"), F), "insp_n_factories"] == 1
    row = out.loc[(pd.Timestamp("2020-09-07"), F)]
    assert row.insp_n_factories == 2 and row.insp_actions_3y == 1
    assert row.insp_share_bad_3y == 1.0  # one failed inspection, one warning letter


def test_clean_is_different_from_unknown():
    out = built()
    t = pd.Timestamp("2020-09-07")
    assert out.loc[(t, CLEAN), "insp_share_bad_3y"] == 0
    assert out.loc[(t, CLEAN), "insp_foreign_share"] == 0
    assert np.isnan(out.loc[(t, UNLINKED), "insp_n_factories"])


def test_no_look_ahead():
    check_point_in_time(inspections.build, sources(), WEEKS, WEEKS[::20])
