"""Supply features from NDC snapshots: counts, exits/entries, no look-ahead."""

import pandas as pd

from rxradar.features import market
from rxradar.features.pit import check_point_in_time

WEEKS = pd.date_range("2020-01-06", periods=120, freq="W-MON")
F, G = "X|TABLET|ORAL", "Y|INJECTION|PARENTERAL"


def ndc():
    rows = []
    s1, s2, s3 = pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01"), pd.Timestamp("2022-01-01")
    for labeler, cat in [("L1", "ANDA"), ("L1", "ANDA"), ("L2", "ANDA"), ("L3", "NDA")]:
        rows.append((s1, F, labeler, cat))
    for labeler, cat in [("L1", "ANDA"), ("L3", "NDA"), ("L4", "ANDA")]:  # L2 exits, L4 enters
        rows.append((s2, F, labeler, cat))
    rows.append((s1, G, "L9", "ANDA"))  # G disappears entirely in s2
    rows.append((s3, F, "L1", "ANDA"))
    df = pd.DataFrame(rows, columns=["published", "family", "LABELERNAME", "MARKETINGCATEGORYNAME"])
    return df.assign(APPLICATIONNUMBER="A", PRODUCTNDC="0000-0000")


def build():
    return market.build({"ndc": ndc()}, WEEKS).set_index(["week", "family"])


def test_counts_concentration_and_staleness():
    out = build()
    row = out.loc[(pd.Timestamp("2020-06-01"), F)]
    assert row.sup_n_labelers == 3 and row.sup_n_generic_labelers == 2
    assert row.sup_n_products == 4 and row.sup_top_share == 0.5
    assert row.sup_has_brand
    assert (
        row.sup_snapshot_age_days == (pd.Timestamp("2020-06-01") - pd.Timestamp("2020-01-01")).days
    )


def test_exits_entries_and_full_exit():
    out = build()
    row = out.loc[(pd.Timestamp("2021-03-01"), F)]
    assert row.sup_exits == 1 and row.sup_entries == 1 and row.sup_n_labelers == 3
    gone = out.loc[(pd.Timestamp("2021-03-01"), G)]
    assert gone.sup_n_labelers == 0 and gone.sup_exits == 1


def test_no_look_ahead():
    check_point_in_time(market.build, {"ndc": ndc()}, WEEKS, WEEKS[::8])
