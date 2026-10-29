"""Listing-name -> family mapping on a tiny synthetic universe."""

import pandas as pd

from rxradar.transform.families import MoietyResolver
from rxradar.transform.listing_names import ListingNameMapper, form_hints

NAMES = pd.DataFrame(
    [
        ("BUPIVACAINE HYDROCHLORIDE", "U1", True),
        ("BUPIVACAINE", "M1", True),
        ("PHENYLEPHRINE HYDROCHLORIDE", "U2", True),
        ("PHENYLEPHRINE", "M2", True),
        ("CHLORIDE", "CL", True),
    ],
    columns=["name_norm", "unii", "preferred"],
)
MOIETY = pd.DataFrame([("U1", "M1"), ("U2", "M2")], columns=["unii", "moiety_unii"])
FAMILIES = [
    "M1|INJECTION|PARENTERAL",
    "M1|SEMISOLID|TOPICAL",
    "M2|LIQUID|OPHTHALMIC",
    "M2|INJECTION|PARENTERAL",
    "M2|TABLET_ER|ORAL",
]


def mapper(**kw):
    return ListingNameMapper(MoietyResolver(NAMES, MOIETY), FAMILIES, overrides={}, **kw)


def test_parsed_uses_form_and_route_words():
    m = mapper()
    assert m.map("Bupivacaine Hydrochloride Injection") == ("M1|INJECTION|PARENTERAL", "parsed")
    assert m.map("Phenylephrine HCl (Mydfrin) Ophthalmic Solution")[0] == "M2|LIQUID|OPHTHALMIC"
    assert m.map("Phenylephrine Hydrochloride XR Tablets")[0] == "M2|TABLET_ER|ORAL"


def test_counter_ion_alone_is_not_an_ingredient():
    assert mapper().ingredients("Unknownium Chloride Injection") == []


def test_ambiguous_names_are_not_guessed():
    assert mapper().map("Bupivacaine") == (None, "ambiguous_form")


def test_openfda_and_override_tiers_win():
    m = ListingNameMapper(
        MoietyResolver(NAMES, MOIETY),
        FAMILIES,
        openfda_names={"Bupivacaine": {"M1|SEMISOLID|TOPICAL"}},
        overrides={"phenylephrine": "M2|INJECTION|PARENTERAL"},
    )
    assert m.map("Bupivacaine") == ("M1|SEMISOLID|TOPICAL", "openfda")
    assert m.map("Phenylephrine") == ("M2|INJECTION|PARENTERAL", "override")


def test_form_hints():
    assert form_hints("Doxorubicin Lyophilized Powder") == ({"INJECTION"}, {"PARENTERAL"})
    assert form_hints("Memantine (Namenda) XR Capsules")[0] == {"CAPSULE_ER"}


def test_release_type_relaxed_only_when_unique():
    m = mapper()
    assert m.map("Phenylephrine Tablets") == ("M2|TABLET_ER|ORAL", "parsed_relaxed_form")


def test_units_and_short_tokens_are_not_ingredients():
    names = pd.concat(
        [
            NAMES,
            pd.DataFrame(
                [("MG", "MAGNESIUM", True), ("ER", "ERBIUM", True)], columns=NAMES.columns
            ),
        ]
    )
    m = ListingNameMapper(MoietyResolver(names, MOIETY), FAMILIES, overrides={})
    assert m.ingredients("Bupivacaine Hydrochloride ER Injection 5 mg/mL") == ["M1"]


def test_diluent_dropped_when_no_combined_family():
    names = pd.concat(
        [NAMES, pd.DataFrame([("SODIUM CHLORIDE", "NACL", True)], columns=NAMES.columns)]
    )
    m = ListingNameMapper(MoietyResolver(names, MOIETY), FAMILIES, overrides={})
    fam, _ = m.map("Bupivacaine Hydrochloride in 0.9per Sodium Chloride Injection")
    assert fam == "M1|INJECTION|PARENTERAL"
