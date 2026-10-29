"""Map FDA shortage-list names ("Bupivacaine Hydrochloride Injection") to
drug families (docs/decisions.md D5).

Archived listings carry only a name, so we resolve them in three tiers, best first:

1. ``openfda``: the name appears as ``generic_name`` in an openFDA shortage
   snapshot, whose package NDCs give the family directly.
2. ``parsed``: find ingredient phrases in the name (longest match against
   every GSRS name and synonym), collapse them to active moieties, then pick
   among the families that really exist with exactly those moieties, using
   form/route words in the name ("injection", "extended release",
   "ophthalmic"...) to break ties.
3. ``override``: a reviewed CSV (``data/listing_name_overrides.csv``) for
   names the first two tiers can't settle. It is applied first, so a human
   decision always wins.

Anything left is reported as unmapped, never guessed.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from importlib import resources

import pandas as pd

from rxradar.transform.families import MoietyResolver

MAX_SPAN = 6
# Words that can be part of an ingredient phrase but are never an active
# ingredient on their own (counter-ions, hydration states, form words).
NOT_ALONE = frozenset(
    """hydrochloride hcl sulfate acetate citrate phosphate mesylate maleate tartrate
    bitartrate succinate besylate bromide chloride anhydrous monohydrate dihydrate
    trihydrate hydrate disodium dibasic monobasic injection tablet tablets capsule
    capsules solution suspension powder oral for and in with usp extended release delayed
    film coated chewable lyophilized ophthalmic otic topical cream ointment gel spray
    nasal inhalation emulsion concentrate premix premixed vial vials single dose multi
    sterile plastic container bag kit liquid syrup elixir drops patch transdermal
    suppository irrigation infusion orally disintegrating""".split()  # noqa: SIM905
)
# Units and abbreviations that collide with GSRS synonyms ("MG" is magnesium,
# "ER" is erbium); never treated as ingredients.
UNIT_TOKENS = frozenset(
    "mg ml mcg kg meq mmol per percent unit units iu er xr xl sr cr la dr eq base usp nf".split()  # noqa: SIM905
)
MIN_SINGLE_WORD = 4
# Diluents that listing names mention but NDC products often don't list as an
# active ingredient ("Heparin Sodium in 0.9% Sodium Chloride").
DILUENT_NAMES = ("DEXTROSE", "SODIUM CHLORIDE", "WATER")


def form_hints(name: str) -> tuple[set[str], set[str]]:
    """Plausible (form groups, route groups) from words in a listing name.

    Empty sets mean "no hint". Hints only *filter* real candidate families;
    they never create a family that doesn't exist in NDC/Drugs@FDA data.
    """
    s = name.lower()
    er = bool(re.search(r"extended[- ]release|\b(er|xl|xr|sr|cr|la)\b", s))
    dr = "delayed" in s
    forms: set[str] = set()
    routes: set[str] = set()
    if "liposom" in s or "lipid complex" in s:
        return {"INJECTION_LIPID"}, {"PARENTERAL"}
    if re.search(r"inject|infusion|\bvial|premix|intravenous|lyophili|ampul|\bsyringe", s):
        return ({"INJECTION_ER"} if er else {"INJECTION"}), {"PARENTERAL"}
    if "ophthalmic" in s:
        routes.add("OPHTHALMIC")
    elif "otic" in s:
        routes.add("OTIC")
    elif "nasal" in s:
        routes.add("NASAL")
    elif re.search(r"inhal|nebuli", s):
        routes.add("INHALATION")
    if "tablet" in s:
        forms.add("TABLET_ER" if er else "TABLET_DR" if dr else "TABLET")
        routes.add("ORAL")
    elif "capsule" in s:
        forms.add("CAPSULE_ER" if er else "CAPSULE_DR" if dr else "CAPSULE")
        routes.add("ORAL")
    elif re.search(r"cream|ointment|gel\b|lotion|jelly", s):
        forms.add("SEMISOLID")
    elif re.search(r"suspension|solution|syrup|elixir|liquid|drops|concentrate", s):
        forms.add("LIQUID_ER" if er else "LIQUID")
    if "suppositor" in s:
        forms.add("SUPPOSITORY")
    if re.search(r"patch|transdermal", s):
        forms.add("TRANSDERMAL")
    if re.search(r"irrigat", s):
        forms.add("IRRIGANT")
    if re.search(r"aerosol|spray|inhaler", s):
        forms.add("AEROSOL_SPRAY")
    if re.search(r"\boral\b|syrup|elixir", s):
        routes.add("ORAL")
    return forms, routes


class ListingNameMapper:
    def __init__(
        self,
        resolver: MoietyResolver,
        families: list[str],
        openfda_names: dict[str, set[str]] | None = None,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self.resolver = resolver
        self.openfda_names = {k.lower(): v for k, v in (openfda_names or {}).items()}
        self.overrides = {k.lower(): v for k, v in (overrides or load_overrides()).items()}
        self.diluents = frozenset(resolver.moiety(n) for n in DILUENT_NAMES)
        self.by_moieties: dict[frozenset[str], set[str]] = defaultdict(set)
        for fam in families:
            self.by_moieties[frozenset(fam.split("|")[0].split("+"))].add(fam)

    def ingredients(self, name: str) -> list[str]:
        """Active moieties found in a listing name (greedy longest match)."""
        s = re.sub(r"\([^)]*\)", " ", name)  # brand names in parentheses
        tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z\-']*", s) if t.lower() not in UNIT_TOKENS]
        found, i = [], 0
        while i < len(tokens):
            for j in range(min(len(tokens), i + MAX_SPAN), i, -1):
                span = tokens[i:j]
                if len(span) == 1 and (
                    span[0].lower() in NOT_ALONE or len(span[0]) < MIN_SINGLE_WORD
                ):
                    continue
                if all(t.lower() in NOT_ALONE for t in span):
                    continue
                unii = self.resolver.unii(" ".join(span))
                if unii is not None:
                    found.append(self.resolver.moiety(" ".join(span)))
                    i = j
                    break
            else:
                i += 1
        return sorted(set(found))

    def map(self, name: str) -> tuple[str | None, str]:
        """Return (family or None, method)."""
        key = name.lower().strip()
        if key in self.overrides:
            fam = self.overrides[key]
            return (fam or None), "override"
        if key in self.openfda_names and len(self.openfda_names[key]) == 1:
            return next(iter(self.openfda_names[key])), "openfda"
        moieties = self.ingredients(name)
        if not moieties:
            return None, "no_ingredient_found"
        candidates = self.by_moieties.get(frozenset(moieties), set())
        if not candidates:
            without = frozenset(moieties) - self.diluents
            if without and without != frozenset(moieties):
                candidates = self.by_moieties.get(without, set())
        if not candidates:
            return None, "no_family_with_these_ingredients"
        forms, routes = form_hints(name)
        narrowed = {
            c
            for c in candidates
            if (not forms or c.split("|")[1] in forms)
            and (not routes or set(c.split("|")[2].split(";")) & routes)
        }
        if len(narrowed) == 1:
            return next(iter(narrowed)), "parsed"
        if len(candidates) == 1 and not forms and not routes:
            return next(iter(candidates)), "parsed_only_family"
        if not narrowed and forms:
            # Names often omit the release type ("Desvenlafaxine Tablets" when
            # only ER tablets exist): accept a single same-base-form variant.
            base = {f.split("_")[0] for f in forms}
            relaxed = {c for c in candidates if c.split("|")[1].split("_")[0] in base}
            if len(relaxed) == 1:
                return next(iter(relaxed)), "parsed_relaxed_form"
        return None, "ambiguous_form" if narrowed else "form_not_found"


def load_overrides() -> dict[str, str]:
    path = resources.files("rxradar.transform") / "data" / "listing_name_overrides.csv"
    with path.open() as fh:
        return {r["listing_name"]: r["family"] for r in csv.DictReader(fh)}


def openfda_name_families(shortage_records: list[dict], product_fams: pd.DataFrame) -> dict:
    """generic_name -> set of families, via each record's package NDC."""
    prod = product_fams.dropna(subset=["family"]).drop_duplicates("PRODUCTNDC")
    ndc_to_fam = dict(zip(prod.PRODUCTNDC, prod.family, strict=True))
    out: dict[str, set[str]] = defaultdict(set)
    for r in shortage_records:
        product_ndc = "-".join(r["package_ndc"].split("-")[:2])
        if product_ndc in ndc_to_fam:
            out[r["generic_name"]].add(ndc_to_fam[product_ndc])
    return dict(out)
