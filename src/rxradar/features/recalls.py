"""Recall features from openFDA drug enforcement reports.

Why: recalls signal quality failures that can take a product (or a whole
plant) off the market. A family's own recalls, and recent recalls by the
labelers that sell it, are both plausible shortage precursors.

Mapping recalls to families uses NDC codes only, best first: the openFDA
``product_ndc`` block, dashed NDCs in the description/code text, then
undashed 11-digit NDCs. Free-text name parsing is deliberately *not* used,
because recall descriptions are noisy enough to create false matches. About
30% of recalls map. The rest are mostly compounding pharmacies (no NDC) and
OTC products (outside our Rx families). Coverage is in docs/data_audit.md.

Point in time: ``published`` = ``report_date`` (weekly enforcement report),
never ``recall_initiation_date`` (median 47 days earlier).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from rxradar.features.common import NS, asof_capture, count_in_window, family_grid

_DASHED = re.compile(r"\b(\d{4,5})-(\d{3,4})(?:-\d{1,2})?\b")
_ELEVEN = re.compile(r"(?<!\d)(\d{11})(?!\d)")


def _eleven_digit_candidates(d: str) -> list[str]:
    """11-digit NDC (5-4-2 padded) -> possible product NDC spellings."""
    a, b = d[:5], d[5:9]
    out = [f"{a}-{b}"]
    if a.startswith("0"):
        out.append(f"{a[1:]}-{b}")
    if b.startswith("0"):
        out.append(f"{a}-{b[1:]}")
    return out


def recall_source(enforcement: list[dict], ndc_to_family: dict[str, str]) -> pd.DataFrame:
    """One row per (recall, family, labeler code), with how it was matched."""
    rows = []
    for r in enforcement:
        text = f"{r.get('product_description', '')} {r.get('code_info', '')}"
        found: dict[str, str] = {}
        for n in (r.get("openfda") or {}).get("product_ndc", []):
            if n in ndc_to_family:
                found.setdefault(n, "openfda")
        if not found:
            for a, b in _DASHED.findall(text):
                if f"{a}-{b}" in ndc_to_family:
                    found.setdefault(f"{a}-{b}", "text_ndc")
        if not found:
            for d in _ELEVEN.findall(text):
                for c in _eleven_digit_candidates(d):
                    if c in ndc_to_family:
                        found.setdefault(c, "text_ndc11")
        for ndc, how in found.items():
            rows.append(
                {
                    "recall_number": r.get("recall_number"),
                    "published": pd.to_datetime(r.get("report_date"), format="%Y%m%d"),
                    "classification": r.get("classification"),
                    "family": ndc_to_family[ndc],
                    "labeler_code": ndc.split("-")[0].lstrip("0"),
                    "matched_by": how,
                }
            )
    out = pd.DataFrame(rows)
    if len(out):
        out["published"] = out.published.astype(NS)
    return out


def build(sources: dict[str, pd.DataFrame], weeks: pd.DatetimeIndex) -> pd.DataFrame:
    rec = sources["recalls"]
    grid = family_grid(sources, weeks)
    if grid.empty:
        return grid
    if rec.empty:
        for c in ("rec_family_1y", "rec_family_class1_1y", "rec_family_90d", "rec_labelers_1y"):
            grid[c] = 0
        return grid
    per_family = rec.drop_duplicates(["recall_number", "family"])
    grid["rec_family_1y"] = count_in_window(grid, per_family, 365)
    grid["rec_family_90d"] = count_in_window(grid, per_family, 90)
    grid["rec_family_class1_1y"] = count_in_window(
        grid, per_family[per_family.classification == "Class I"], 365
    )

    # Recalls of *any* product by labelers selling this family, using the
    # labeler codes in the latest NDC snapshot on or before t.
    ndc = sources.get("ndc")
    if ndc is None or ndc.empty:
        grid["rec_labelers_1y"] = np.nan
        return grid
    labs = ndc.assign(labeler_code=ndc.PRODUCTNDC.str.split("-").str[0].str.lstrip("0"))
    labs = labs[["published", "family", "labeler_code"]].drop_duplicates()
    snaps = np.sort(labs.published.unique())
    g = grid[["week", "family"]].assign(row=np.arange(len(grid)))
    g["snapshot"] = asof_capture(g.week, snaps)
    by_lab = rec.drop_duplicates(["recall_number", "labeler_code"])
    lab_counts = g.merge(labs.rename(columns={"published": "snapshot"}), on=["snapshot", "family"])[
        ["row", "week", "labeler_code"]
    ]
    lab_counts["n"] = count_in_window(lab_counts, by_lab, 365, by="labeler_code")
    grid["rec_labelers_1y"] = (
        lab_counts.groupby("row").n.sum().reindex(range(len(grid)), fill_value=0).values
    )
    grid.loc[g.snapshot.isna().values, "rec_labelers_1y"] = np.nan
    return grid


def ndc_to_family_map(products: pd.DataFrame, ndc_snapshots: pd.DataFrame) -> dict[str, str]:
    m = dict(zip(products.PRODUCTNDC, products.family, strict=True))
    m.update(dict(zip(ndc_snapshots.PRODUCTNDC, ndc_snapshots.family, strict=True)))
    return m
