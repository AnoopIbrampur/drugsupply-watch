"""Family key tests: salts collapse, forms group, injectables share a route."""

import pandas as pd

from rxradar.transform.families import (
    MoietyResolver,
    form_group,
    form_groups,
    product_families,
    route_group,
)

NAMES = pd.DataFrame(
    [
        ("DEXTROSE MONOHYDRATE", "LX22YL083G", True),
        ("DEXTROSE", "5SL0G7R0OK", True),
        ("ANHYDROUS DEXTROSE", "5SL0G7R0OK", False),
        ("MORPHINE SULFATE", "X3P646A2J0", True),
        ("MORPHINE", "76I7G6D29C", True),
        # "GLUCOSE" is a synonym of dextrose and also, say, a non-preferred
        # name of something else: the preferred match must win.
        ("GLUCOSE", "WRONG00000", False),
        ("GLUCOSE", "5SL0G7R0OK", True),
    ],
    columns=["name_norm", "unii", "preferred"],
)
MOIETY = pd.DataFrame(
    [("LX22YL083G", "5SL0G7R0OK"), ("X3P646A2J0", "76I7G6D29C")],
    columns=["unii", "moiety_unii"],
)


def test_every_crosswalk_row_has_a_group():
    groups = form_groups()
    assert len(groups) > 100
    assert all(groups.values())


def test_form_group_examples():
    assert form_group("Tablet, Film Coated") == "TABLET"
    assert form_group("TABLET, FILM COATED, EXTENDED RELEASE") == "TABLET_ER"
    assert form_group("INJECTION, POWDER, LYOPHILIZED, FOR SOLUTION") == "INJECTION"
    assert form_group("INJECTION, SUSPENSION, LIPOSOMAL") == "INJECTION_LIPID"
    assert form_group("SOMETHING NEW") == "UNMAPPED"


def test_route_group_collapses_parenteral():
    assert route_group("INTRAMUSCULAR; INTRAVENOUS", "INJECTION") == "PARENTERAL"
    assert route_group("INTRAVENOUS", "INJECTION") == "PARENTERAL"
    assert route_group("ORAL", "TABLET") == "ORAL"
    assert route_group("OPHTHALMIC", "LIQUID") == "OPHTHALMIC"


def test_salts_and_hydrates_collapse_to_moiety():
    r = MoietyResolver(NAMES, MOIETY)
    assert r.moiety("Dextrose Monohydrate") == r.moiety("DEXTROSE") == "5SL0G7R0OK"
    assert r.moiety("morphine sulfate") == "76I7G6D29C"
    assert r.moiety("glucose") == "5SL0G7R0OK"
    assert r.moiety("Unknownium Chloride") == "NAME:UNKNOWNIUM CHLORIDE"


def test_product_families_end_to_end():
    products = pd.DataFrame(
        {
            "SUBSTANCENAME": ["DEXTROSE MONOHYDRATE", "DEXTROSE", "DEXTROSE", ""],
            "DOSAGEFORMNAME": ["INJECTION, SOLUTION", "INJECTION", "TABLET", "TABLET"],
            "ROUTENAME": ["INTRAVENOUS", "INTRAMUSCULAR; INTRAVENOUS", "ORAL", "ORAL"],
        }
    )
    fam = product_families(products, MoietyResolver(NAMES, MOIETY)).family.tolist()
    assert fam[0] == fam[1] == "5SL0G7R0OK|INJECTION|PARENTERAL"
    assert fam[2] == "5SL0G7R0OK|TABLET|ORAL"
    assert pd.isna(fam[3])  # no ingredients -> no family


def test_drugsfda_spellings():
    assert form_group("INJECTABLE") == "INJECTION"
    assert form_group("CAPSULE, DELAYED REL PELLETS") == "CAPSULE_DR"
    assert route_group("ORAL-28", "TABLET") == "ORAL"
    assert route_group("INJECTION", "INJECTION") == "PARENTERAL"


def test_delisted_products_resolve_through_application():
    from rxradar.transform.families import resolve_delisted

    app_fams = pd.DataFrame(
        {
            "APPLICATIONNUMBER": ["ANDA1", "NDA2", "NDA2", "NDA3", "NDA3"],
            "brand": ["X", "ORALBRAND", "SOLBRAND", "SAME", "SAME"],
            "family": ["F|TABLET|ORAL", "G|TABLET|ORAL", "G|LIQUID|ORAL", "H|A|B", "H|C|D"],
        }
    )
    exc = pd.DataFrame(
        {
            "APPLICATIONNUMBER": ["ANDA1", "NDA2", "NDA3", "ANDA9"],
            "PROPRIETARYNAME": ["anything", "SolBrand", "Same", "x"],
        }
    )
    out = resolve_delisted(exc, app_fams)
    assert out.family.tolist()[:2] == ["F|TABLET|ORAL", "G|LIQUID|ORAL"]
    assert out.family_source.tolist() == [
        "drugsfda",
        "drugsfda_brand",
        "ambiguous_application",
        "no_drugsfda_match",
    ]
