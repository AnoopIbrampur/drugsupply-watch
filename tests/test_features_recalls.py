"""Recall mapping and features: NDC matching tiers, windows, no look-ahead."""

import pandas as pd

from rxradar.features import recalls
from rxradar.features.pit import check_point_in_time

NDC_MAP = {"0409-4888": "X|INJECTION|PARENTERAL", "12345-678": "Y|TABLET|ORAL"}


def enforcement():
    return [
        {  # openfda block
            "recall_number": "D-1",
            "report_date": "20200115",
            "recall_initiation_date": "20191201",
            "classification": "Class I",
            "openfda": {"product_ndc": ["0409-4888"]},
            "product_description": "X injection",
        },
        {  # dashed NDC in text, same labeler code as nothing else
            "recall_number": "D-2",
            "report_date": "20200401",
            "classification": "Class II",
            "product_description": "Y tablets NDC 12345-678-01",
        },
        {  # undashed 11-digit NDC, leading zero labeler
            "recall_number": "D-3",
            "report_date": "20200601",
            "classification": "Class II",
            "product_description": "Other",
            "code_info": "NDC 00409488801",
        },
        {  # compounder, no NDC: unmatched
            "recall_number": "D-4",
            "report_date": "20200601",
            "classification": "Class II",
            "product_description": "Tri-MIX injectable syringes",
        },
    ]


def sources():
    rec = recalls.recall_source(enforcement(), NDC_MAP)
    ndc = pd.DataFrame(
        {
            "published": pd.to_datetime(["2019-01-01", "2019-01-01"]),
            "family": ["X|INJECTION|PARENTERAL", "Z|TABLET|ORAL"],
            "PRODUCTNDC": ["0409-4888", "0409-1111"],  # Z shares labeler 409 with X
        }
    )
    return {"recalls": rec, "ndc": ndc}


def test_matching_tiers():
    rec = recalls.recall_source(enforcement(), NDC_MAP)
    assert dict(zip(rec.recall_number, rec.matched_by, strict=True)) == {
        "D-1": "openfda",
        "D-2": "text_ndc",
        "D-3": "text_ndc11",
    }
    # Published is the report date, not the initiation date.
    assert rec.loc[rec.recall_number == "D-1", "published"].iloc[0] == pd.Timestamp("2020-01-15")


def test_family_and_labeler_counts():
    weeks = pd.date_range("2019-01-07", periods=100, freq="W-MON")
    out = recalls.build(sources(), weeks).set_index(["week", "family"])
    t = pd.Timestamp("2020-06-08")
    x = out.loc[(t, "X|INJECTION|PARENTERAL")]
    assert x.rec_family_1y == 2 and x.rec_family_class1_1y == 1
    # Z has no own recalls but its labeler (409) had two.
    assert out.loc[(t, "Z|TABLET|ORAL"), "rec_labelers_1y"] == 2
    assert out.loc[(t, "Z|TABLET|ORAL"), "rec_family_1y"] == 0


def test_no_look_ahead():
    weeks = pd.date_range("2019-01-07", periods=100, freq="W-MON")
    check_point_in_time(recalls.build, sources(), weeks, weeks[::7])
