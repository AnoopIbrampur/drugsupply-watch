"""Display names prefer FDA's wording; alternatives stay within one ingredient."""

import pandas as pd

from rxradar.site.catalog import alternatives, display_names, form_label

INJ, TAB, OTHER = "M1|INJECTION|PARENTERAL", "M1|TABLET|ORAL", "M2|LIQUID|OPHTHALMIC"


def test_name_priority():
    listings = pd.DataFrame(
        {
            "published": pd.to_datetime(["2020-01-01", "2023-09-18"]),
            "name": ["Drugx Injection", "Drugx Injection, Solution"],
            "family": [INJ, INJ],
        }
    )
    products = pd.DataFrame(
        {
            "NONPROPRIETARYNAME": ["drugx hydrochloride", "DRUGX HYDROCHLORIDE", "other"],
            "family": [TAB, TAB, INJ],
        }
    )
    names = display_names(listings, products, [INJ, TAB, OTHER])
    assert names[INJ] == "Drugx Injection, Solution"  # FDA's latest wording wins
    assert names[TAB] == "Drugx Hydrochloride tablet"
    assert names[OTHER] == "M2 liquid (ophthalmic)"


def test_form_labels():
    assert form_label(INJ) == "injection"
    assert form_label("X|TABLET_ER|ORAL") == "extended-release tablet"
    assert form_label(OTHER) == "liquid (ophthalmic)"


def test_alternatives_only_same_ingredient():
    ndc = pd.DataFrame({"family": [INJ, INJ, TAB, OTHER], "LABELERNAME": ["A", "B", "C", "D"]})
    alt = alternatives(ndc, pd.Series({INJ: "inj", TAB: "tab", OTHER: "other"}))
    assert alt[INJ]["labelers"] == ["A", "B"]
    assert alt[INJ]["other_forms"] == [{"family": TAB, "name": "tab", "n_labelers": 1}]
    assert alt[OTHER]["other_forms"] == []


def test_fda_percent_encoding_is_restored():
    from rxradar.site.catalog import clean_fda_name

    assert clean_fda_name("Sodium Chloride 0.9per Injection") == "Sodium Chloride 0.9% Injection"
    assert clean_fda_name("Nitroglycerin in 5chr(37) Dextrose") == "Nitroglycerin in 5% Dextrose"
    assert clean_fda_name("Peroxide Superior") == "Peroxide Superior"  # 'per' inside words stays
