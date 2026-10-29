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


def route_group(route: str, form: str) -> str:
    routes = {r.strip().upper() for r in re.split(r"[;,]", route) if r.strip()}
    if form.startswith("INJECTION") or routes & PARENTERAL_ROUTES:
        return "PARENTERAL"
    return ";".join(sorted(routes)) or "UNKNOWN"


class MoietyResolver:
    """Ingredient name -> active-moiety UNII, built from substance tables."""

    def __init__(self, names: pd.DataFrame, moiety: pd.DataFrame) -> None:
        # When a name maps to several substances, the one where it is the
        # *preferred* name wins: preferred rows come last and overwrite.
        ordered = names.sort_values("preferred", kind="stable")
        self.name_to_unii = dict(zip(ordered.name_norm, ordered.unii, strict=True))
        self.unii_to_moiety = dict(zip(moiety.unii, moiety.moiety_unii, strict=True))

    def unii(self, name: str) -> str | None:
        return self.name_to_unii.get(normalize_substance_name(name))

    def moiety(self, name: str) -> str:
        u = self.unii(name)
        if u is None:
            return "NAME:" + normalize_substance_name(name)
        # A substance with no ACTIVE MOIETY link is its own moiety.
        return self.unii_to_moiety.get(u, u)


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
    out["moieties"] = [
        sorted({resolver.moiety(n) for n in s.split(";") if n.strip()}) if s else []
        for s in out.SUBSTANCENAME
    ]
    out["family"] = [
        family_key(m, f, r) if m else None
        for m, f, r in zip(out.moieties, out.form_group, out.route_group, strict=True)
    ]
    return out
