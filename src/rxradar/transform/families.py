"""Drug-family key: active moieties + coarse dosage form + route group.

Decided in docs/decisions.md D3. Example:
``5SL0G7R0OK|INJECTION|PARENTERAL`` is dextrose injection, whatever the salt,
hydrate, strength, bag size or labeler.

Pieces:
- Ingredient names -> UNII -> active-moiety UNII via the substance tables
  (transform/substances.py). A name we can't resolve keeps a readable
  ``NAME:<name>`` placeholder so the product still gets a family key, and the
  failure shows up in coverage reports instead of silently merging families.
- Dosage form -> coarse group via the reviewed crosswalk in
  ``data/dosage_form_groups.csv``. Unknown forms map to ``UNMAPPED``.
- Route -> group. All injectables collapse to ``PARENTERAL`` so that
  "INTRAVENOUS" and "INTRAMUSCULAR; INTRAVENOUS" vials of one drug are the same
  family, as FDA treats them on its shortage list.
"""

from __future__ import annotations

import csv
import re
from functools import cache
from importlib import resources

import pandas as pd

from rxradar.transform.substances import normalize_substance_name

PARENTERAL_ROUTES = frozenset(
    """INTRAVENOUS INTRAMUSCULAR SUBCUTANEOUS INTRADERMAL INTRATHECAL EPIDURAL
    INTRA-ARTICULAR INTRA-ARTERIAL INTRACARDIAC INTRACAVERNOUS INTRACAMERAL
    INTRALESIONAL INTRAOCULAR INTRAOSSEOUS INTRAPERITONEAL INTRAPLEURAL INTRASPINAL
    INTRAVENTRICULAR INTRAVITREAL INFILTRATION PERINEURAL SUBARACHNOID RETROBULBAR
    SUBCONJUNCTIVAL INTRASYNOVIAL INTRABURSAL INTRACAVITARY INTRATYMPANIC
    INTRACORONARY INTRAVASCULAR""".split()  # noqa: SIM905 - word list reads better
)


@cache
def form_groups() -> dict[str, str]:
    path = resources.files("rxradar.transform") / "data" / "dosage_form_groups.csv"
    with path.open() as fh:
        return {row["dosage_form"]: row["form_group"] for row in csv.DictReader(fh)}


def form_group(dosage_form: str) -> str:
    return form_groups().get(" ".join(dosage_form.upper().split()), "UNMAPPED")


# Same route, different spelling in NDC vs Drugs@FDA.
ROUTE_SYNONYMS = {
    "RESPIRATORY (INHALATION)": "INHALATION",
    "INTRABRONCHIAL": "INHALATION",
    "ORAL-21": "ORAL",
}
# Forms that, given by a parenteral route, are injections in practice (e.g.
# Drugs@FDA lists premixed IV bags as "SOLUTION" + "INTRAVENOUS").
PARENTERAL_AS_INJECTION = {"LIQUID", "POWDER", "OTHER", "UNMAPPED", "KIT"}


def route_group(route: str, form: str) -> str:
    # Drugs@FDA writes cycle packs as "ORAL-28"; the suffix is not a route.
    cleaned = (re.sub(r"-\d+$", "", r.strip().upper()) for r in re.split(r"[;,]", route))
    routes = {ROUTE_SYNONYMS.get(r, r) for r in cleaned if r}
    if form.startswith("INJECTION") or routes & PARENTERAL_ROUTES:
        return "PARENTERAL"
    return ";".join(sorted(routes)) or "UNKNOWN"


def resolve_moiety_links(moiety: pd.DataFrame) -> dict[str, str]:
    """Pick one active moiety per substance from GSRS ACTIVE MOIETY links.

    GSRS sometimes lists several "active moieties" for one substance. For
    example, gemcitabine lists its active metabolites (di-/triphosphate), and
    taking the last link would move gemcitabine into a family of its own.
    Rules, in order:
    1. A substance that is anyone's moiety (including its own) is its own moiety.
    2. A single link is used as given.
    3. Among several links, use the one target that is itself a moiety.
    4. Otherwise keep the substance itself rather than guess.
    """
    known_moieties = set(moiety.moiety_unii)
    self_moieties = set(moiety.unii[moiety.unii == moiety.moiety_unii])
    out = {}
    for unii, targets in moiety.groupby("unii").moiety_unii:
        targets = sorted(set(targets))
        if unii in known_moieties:
            out[unii] = unii
        elif len(targets) == 1:
            out[unii] = targets[0]
        else:
            selfs = [t for t in targets if t in self_moieties]
            out[unii] = selfs[0] if len(selfs) == 1 else unii
    return out


class MoietyResolver:
    """Ingredient name -> active-moiety UNII, built from substance tables."""

    def __init__(self, names: pd.DataFrame, moiety: pd.DataFrame) -> None:
        # When a name maps to several substances, the one where it is the
        # *preferred* name wins: preferred rows come last and overwrite.
        ordered = names.sort_values("preferred", kind="stable")
        self.name_to_unii = dict(zip(ordered.name_norm, ordered.unii, strict=True))
        self.unii_to_moiety = resolve_moiety_links(moiety)
        # Electrolytes (potassium chloride vs acetate vs phosphate) are
        # different drugs whose GSRS moiety is a bare ion; collapsing them
        # would merge distinct shortages. Salts whose moiety is an ion keep
        # their own UNII.
        is_ion = names.name_norm.str.contains(r"\b(?:ION|CATION|ANION)$", regex=True)
        self.ion_uniis = frozenset(names.unii[is_ion])

    # GSRS sometimes names only the hydrate/anhydrous forms (no plain
    # "AZITHROMYCIN"). These suffixes resolve to the same active moiety, so
    # retrying with them can't move a drug into the wrong family.
    FORM_SUFFIXES = ("", " ANHYDROUS", " DIHYDRATE", " MONOHYDRATE", " TRIHYDRATE")

    def unii(self, name: str) -> str | None:
        base = normalize_substance_name(name)
        for suffix in self.FORM_SUFFIXES:
            if (u := self.name_to_unii.get(base + suffix)) is not None:
                return u
        return None

    def moiety(self, name: str) -> str:
        u = self.unii(name)
        if u is None:
            return "NAME:" + normalize_substance_name(name)
        # A substance with no ACTIVE MOIETY link is its own moiety.
        m = self.unii_to_moiety.get(u, u)
        return u if m in self.ion_uniis else m


def family_key(moieties: list[str], form: str, route: str) -> str:
    return "+".join(sorted(set(moieties))) + "|" + form + "|" + route


def product_families(products: pd.DataFrame, resolver: MoietyResolver) -> pd.DataFrame:
    """Add form_group, route_group, moieties and family to NDC product rows.

    Expects the NDC text-file columns SUBSTANCENAME (``A; B``),
    DOSAGEFORMNAME and ROUTENAME.
    """
    out = products.copy()
    out["form_group"] = out.DOSAGEFORMNAME.map(form_group)
    out["route_group"] = [
        route_group(r, f) for r, f in zip(out.ROUTENAME, out.form_group, strict=True)
    ]
    as_injection = (out.route_group == "PARENTERAL") & out.form_group.isin(PARENTERAL_AS_INJECTION)
    out.loc[as_injection, "form_group"] = "INJECTION"
    out["moieties"] = [
        sorted({resolver.moiety(n) for n in s.split(";") if n.strip()}) if s else []
        for s in out.SUBSTANCENAME
    ]
    out["family"] = [
        family_key(m, f, r) if m else None
        for m, f, r in zip(out.moieties, out.form_group, out.route_group, strict=True)
    ]
    return out


RX_APPLICATION_CATEGORIES = ("ANDA", "NDA", "NDA AUTHORIZED GENERIC", "BLA")


def application_families(drugsfda: list[dict], resolver: MoietyResolver) -> pd.DataFrame:
    """One row per Drugs@FDA product: application number, brand name, family."""
    rows = [
        {
            "APPLICATIONNUMBER": app["application_number"],
            "brand": (prod.get("brand_name") or "").upper().strip(),
            "SUBSTANCENAME": "; ".join(i["name"] for i in prod.get("active_ingredients") or []),
            "DOSAGEFORMNAME": prod.get("dosage_form") or "",
            "ROUTENAME": prod.get("route") or "",
        }
        for app in drugsfda
        for prod in app.get("products") or []
    ]
    return product_families(pd.DataFrame(rows), resolver)[["APPLICATIONNUMBER", "brand", "family"]]


def resolve_delisted(excluded: pd.DataFrame, app_fams: pd.DataFrame) -> pd.DataFrame:
    """Give delisted NDC products a family through their application number.

    The excluded-products file has no ingredient, form or route (all blank),
    but Rx products carry an ANDA/NDA/BLA number. An application usually holds
    one family; when it holds several (e.g. tablets and an oral solution
    under one NDA) we disambiguate by brand name, and otherwise leave the
    product unassigned rather than guess.
    """
    fams = app_fams.dropna(subset=["family"])
    by_app = fams.groupby("APPLICATIONNUMBER").family.agg(lambda f: sorted(set(f)))
    by_brand = fams.groupby(["APPLICATIONNUMBER", "brand"]).family.agg(lambda f: sorted(set(f)))
    families, sources = [], []
    for app, brand in zip(excluded.APPLICATIONNUMBER, excluded.PROPRIETARYNAME, strict=True):
        options = by_app.get(app)
        if options is None:
            families.append(None)
            sources.append("no_drugsfda_match")
        elif len(options) == 1:
            families.append(options[0])
            sources.append("drugsfda")
        else:
            narrowed = by_brand.get((app, brand.upper().strip()), [])
            families.append(narrowed[0] if len(narrowed) == 1 else None)
            sources.append("drugsfda_brand" if len(narrowed) == 1 else "ambiguous_application")
    return excluded.assign(family=families, family_source=sources)


def build_product_family_table() -> pd.DataFrame:
    """Family key for every Rx product in the NDC text files, current and
    delisted (``ndc_excluded``), saved to data/derived/product_families.parquet.

    Current products get their family from their own ingredient/form/route
    fields; delisted products through Drugs@FDA (see ``resolve_delisted``).
    Marketing start/end dates are kept so later steps can ask "which labelers
    sold this family at week t?".
    """
    from rxradar.ingest.fda_registry import read_tsv
    from rxradar.ingest.openfda import load_results
    from rxradar.paths import derived_dir, latest_snapshot_dir
    from rxradar.transform.substances import load_tables

    resolver = MoietyResolver(*load_tables(latest_snapshot_dir("openfda_substance")))
    ndc_dir = latest_snapshot_dir("fda_ndc_text")

    cur = read_tsv(ndc_dir / "product.txt")
    cur = cur[cur.PRODUCTTYPENAME == "HUMAN PRESCRIPTION DRUG"]
    cur = product_families(cur, resolver).assign(listing="current", family_source="ndc")

    exc = read_tsv(ndc_dir / "Products_excluded.txt")
    exc = exc[exc.MARKETINGCATEGORYNAME.isin(RX_APPLICATION_CATEGORIES)]
    app_fams = application_families(load_results(latest_snapshot_dir("openfda_drugsfda")), resolver)
    exc = resolve_delisted(exc, app_fams).assign(listing="excluded")

    keep = [
        "PRODUCTID", "PRODUCTNDC", "LABELERNAME", "PROPRIETARYNAME", "NONPROPRIETARYNAME",
        "PHARM_CLASSES",
        "APPLICATIONNUMBER",
        "MARKETINGCATEGORYNAME", "STARTMARKETINGDATE", "ENDMARKETINGDATE", "NDC_EXCLUDE_FLAG",
        "listing", "family", "family_source",
    ]  # fmt: skip
    out = pd.concat([cur.reindex(columns=keep), exc.reindex(columns=keep)], ignore_index=True)
    out.to_parquet(derived_dir() / "product_families.parquet")
    return out
