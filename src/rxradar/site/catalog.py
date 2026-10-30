"""Readable names and same-ingredient alternatives for every drug family.

Names: GSRS "preferred" names are often trade or research codes (e.g.
"SANVAL" for zolpidem), so they are never shown. A family's name is, in
order of preference:
1. FDA's own most recent shortage-list name for it ("Bupivacaine
   Hydrochloride Injection");
2. the most common generic (nonproprietary) name among currently listed NDC
   products, plus a readable form;
3. the ingredient names from Drugs@FDA, plus form.

Alternatives are only other *manufacturers* of the same family and other
*forms* of the same active ingredient(s), from the latest NDC snapshot. They
are supply facts, not clinical substitutes; the site says so on every page.
"""

from __future__ import annotations

import pandas as pd

FORM_LABELS = {
    "TABLET": "tablet",
    "TABLET_ER": "extended-release tablet",
    "TABLET_DR": "delayed-release tablet",
    "CAPSULE": "capsule",
    "CAPSULE_ER": "extended-release capsule",
    "CAPSULE_DR": "delayed-release capsule",
    "INJECTION": "injection",
    "INJECTION_ER": "extended-release injection",
    "INJECTION_LIPID": "liposomal / lipid injection",
    "LIQUID": "liquid",
    "LIQUID_ER": "extended-release liquid",
    "SEMISOLID": "cream / ointment / gel",
    "TRANSDERMAL": "patch",
    "AEROSOL_SPRAY": "spray / aerosol",
    "INHALANT": "inhalation powder",
    "SUPPOSITORY": "suppository",
    "IRRIGANT": "irrigation solution",
    "KIT": "kit",
    "IMPLANT_DEVICE": "implant / insert",
    "FILM": "film",
    "GRANULE": "granules",
    "GRANULE_DR": "delayed-release granules",
    "GRANULE_ER": "extended-release granules",
    "POWDER": "powder",
    "LOZENGE": "lozenge",
    "GAS": "medical gas",
    "ENEMA": "enema",
    "APPLICATOR": "applicator",
    "OTHER": "other form",
    "UNMAPPED": "other form",
}
ROUTE_LABELS = {"PARENTERAL": "injectable", "ORAL": "oral"}


def form_label(family: str) -> str:
    _, form, route = family.split("|")
    label = FORM_LABELS.get(form, form.lower())
    route_l = ROUTE_LABELS.get(route, route.lower().replace(";", ", "))
    if form.startswith("INJECTION") or (route == "ORAL" and form != "LIQUID"):
        return label
    return f"{label} ({route_l})"


def _title(name: str) -> str:
    small = {"and", "in", "for", "with", "of"}
    words = name.lower().split()
    return " ".join(w if w in small and i else w.capitalize() for i, w in enumerate(words))


def display_names(
    listings: pd.DataFrame, current_products: pd.DataFrame, families: list[str]
) -> pd.Series:
    """family -> display name.

    ``listings``: archive rows with published, name, family.
    ``current_products``: current NDC products with NONPROPRIETARYNAME, family.
    """
    names: dict[str, str] = {}
    lst = listings.dropna(subset=["family"]).sort_values("published")
    names.update(lst.groupby("family").name.last().to_dict())
    cp = current_products.dropna(subset=["family"])
    cp = cp[cp.NONPROPRIETARYNAME.str.len() > 0]
    common = cp.groupby("family").NONPROPRIETARYNAME.agg(lambda s: s.str.upper().mode().iloc[0])
    for fam, generic in common.items():
        names.setdefault(fam, f"{_title(generic)} {form_label(fam)}")
    for fam in families:
        names.setdefault(fam, f"{fam.split('|')[0]} {form_label(fam)}")  # last resort: UNIIs
    return pd.Series(names, name="name")


def alternatives(ndc_latest: pd.DataFrame, names: pd.Series) -> dict[str, dict]:
    """family -> {"labelers": [...], "other_forms": [{family, name, n_labelers}]}.

    ``ndc_latest``: rows of the latest NDC snapshot (family, LABELERNAME).
    """
    labelers = ndc_latest.groupby("family").LABELERNAME.agg(lambda s: sorted(set(s)))
    moieties = pd.Series({f: f.split("|")[0] for f in labelers.index})
    by_moiety = moieties.groupby(moieties).groups
    out = {}
    for fam, labs in labelers.items():
        siblings = [f for f in by_moiety[moieties[fam]] if f != fam]
        out[fam] = {
            "labelers": labs,
            "other_forms": [
                {"family": f, "name": names.get(f, f), "n_labelers": len(labelers[f])}
                for f in sorted(siblings, key=lambda f: -len(labelers[f]))
            ],
        }
    return out
