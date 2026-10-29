"""History features: values on a small synthetic archive, and no look-ahead."""

import pandas as pd

from rxradar.features import history
from rxradar.features.pit import check_point_in_time

CAPS = pd.date_range("2020-01-06", periods=40, freq="7D")


def listings():
    rows = [(c, "Filler Name", "Current", "Z|TABLET|ORAL") for c in CAPS]
    rows += [(c, "Drug A Injection", "Current", "A|INJECTION|PARENTERAL") for c in CAPS[10:15]]
    rows += [(c, "Drug A Tablet", "Current", "A|TABLET|ORAL") for c in CAPS[20:30]]
    rows += [(CAPS[25], "Drug B Tablet", "To Be Discontinued", "B|TABLET|ORAL")]
    rows += [(c, "Unmapped Thing", "Current", None) for c in CAPS[::3]]
    return pd.DataFrame(rows, columns=["published", "name", "status", "family"])


def sources():
    products = pd.DataFrame(
        {
            "family": ["A|INJECTION|PARENTERAL", "A|TABLET|ORAL", "B|TABLET|ORAL"],
            "published": pd.to_datetime(["2019-01-01"] * 3),
        }
    )
    return {"listings": listings(), "products": products}


def test_values():
    out = history.build(sources(), pd.DatetimeIndex(CAPS)).set_index(["week", "family"])
    inj, tab = "A|INJECTION|PARENTERAL", "A|TABLET|ORAL"
    # While the injection is short, the tablet sees one sibling short.
    assert out.loc[(CAPS[12], tab), "hist_siblings_short_now"] == 1
    assert out.loc[(CAPS[17], tab), "hist_siblings_short_now"] == 0
    assert out.loc[(CAPS[17], tab), "hist_sibling_onsets_1y"] == 1
    # Own history after the injection episode.
    assert out.loc[(CAPS[16], inj), "hist_onsets_all"] == 1
    assert out.loc[(CAPS[16], inj), "hist_days_since_listed"] == 14
    assert out.loc[(CAPS[9], inj), "hist_onsets_all"] == 0
    assert out.loc[(CAPS[26], "B|TABLET|ORAL"), "hist_disc_notices_1y"] == 1
    assert out.loc[(CAPS[12], tab), "mkt_families_short_now"] == 2  # filler + injection


def test_no_look_ahead():
    check_point_in_time(history.build, sources(), pd.DatetimeIndex(CAPS), CAPS[::4])
