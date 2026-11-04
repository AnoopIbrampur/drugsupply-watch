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

import csv
import re
from functools import cache
from importlib import resources

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


OTHER_GROUP = "Other"
# Products defined by where they're used, whatever their pharmacology.
ROUTE_GROUPS = {
    "OPHTHALMIC": "Eye",
    "OTIC": "Ear",
    "TOPICAL": "Skin",
    "INHALATION": "Allergy & breathing",
    "NASAL": "Allergy & breathing",
}
NUTRITION_GROUP = "Vitamins, nutrition & electrolytes"
# Mixes of many ingredients (multivitamins, IV nutrition, homeopathic blends) carry
# dozens of classes, so their first class match means little.
MANY_INGREDIENTS = 5
NUTRITION_CLASSES = ("Vitamin", "Amino Acid", "Potassium Salt", "Osmotic Laxative", "Calcium")
# Common IV fluids have no pharmacologic class in the NDC directory.
FLUID_INGREDIENTS = ("SODIUM CHLORIDE", "DEXTROSE", "WATER", "LACTATE", "RINGER")


@cache
def group_rules() -> tuple[tuple[str, str], ...]:
    path = resources.files("rxradar.transform") / "data" / "drug_group_rules.csv"
    with path.open(encoding="utf-8") as fh:
        return tuple((r["pattern"], r["group"]) for r in csv.DictReader(fh))


@cache
def group_names() -> tuple[tuple[str, str], ...]:
    path = resources.files("rxradar.transform") / "data" / "drug_group_names.csv"
    with path.open(encoding="utf-8") as fh:
        return tuple((r["name_contains"], r["group"]) for r in csv.DictReader(fh))


def drug_group(family: str, classes: str, generic_name: str = "") -> str:
    """One broad browsing group for a family, from its EPC classes.

    Rules are ordered and the first match wins, because combination products
    carry several classes. Groups are for browsing only: being in one group
    doesn't mean two drugs can substitute for each other.
    """
    moieties, _, route = family.split("|")
    if route in ROUTE_GROUPS:
        return ROUTE_GROUPS[route]
    upper = generic_name.upper()
    for fragment, group in group_names():
        if fragment in upper:
            return group
    epc = [c.strip() for c in classes.split(",") if "[EPC]" in c]
    if moieties.count("+") + 1 >= MANY_INGREDIENTS:
        nutrition = any(n in c for n in NUTRITION_CLASSES for c in epc)
        return NUTRITION_GROUP if nutrition else OTHER_GROUP
    for pattern, group in group_rules():
        if any(pattern in c for c in epc):
            return group
    if any(w in upper for w in FLUID_INGREDIENTS):
        return NUTRITION_GROUP
    return OTHER_GROUP


def clean_fda_name(name: str) -> str:
    """FDA's list encodes "%" in names as "per" or "chr(37)" ("Sodium
    Chloride 0.9per Injection"); restore it for display."""
    name = name.replace("chr(37)", "%")
    return re.sub(r"(\d)\s?per\b", r"\1%", name)


def display_names(
    listings: pd.DataFrame, current_products: pd.DataFrame, families: list[str]
) -> pd.Series:
    """family -> display name.

    ``listings``: archive rows with published, name, family.
    ``current_products``: current NDC products with NONPROPRIETARYNAME, family.
    """
    names: dict[str, str] = {}
    lst = listings.dropna(subset=["family"]).sort_values("published")
    names.update(lst.groupby("family").name.last().map(clean_fda_name).to_dict())
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


def company_key(name: str) -> str:
    """First word of a company name, so "Hikma Pharmaceuticals USA Inc." and
    "Hikma Pharmaceuticals" count as one company when comparing snapshots."""
    words = re.findall(r"[A-Z0-9]+", name.upper())
    return words[0] if words else ""


def lost_companies(old: pd.DataFrame, new: pd.DataFrame) -> dict[str, list[str]]:
    """family -> companies in the older NDC snapshot but gone from the newer one.

    Only families still listed count: a family that vanished entirely is more
    often a relisting than a market exit.
    """
    before = old.groupby("family").LABELERNAME.agg(set)
    after = new.groupby("family").LABELERNAME.agg(set)
    out = {}
    for fam, names in before.items():
        if fam not in after:
            continue
        keep = {company_key(n) for n in after[fam]}
        gone = {company_key(n): n for n in sorted(names) if company_key(n) not in keep}
        if gone:
            out[fam] = sorted(gone.values())
    return out
