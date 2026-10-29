"""Phase 1 audit 5c: can NDC labelers be linked to FDA establishment (FEI) records?

FDA inspections and compliance actions are keyed by FEI. The NDC directory
names a labeler but carries no FEI, so we try to match labeler names against
the Drug Establishments Current Registration Site (DECRS) firm and registrant
names. Prints match rates and failure cases for a fixed random sample of 50
labelers that appear in the openFDA shortage file.
"""

import re
import sys

import pandas as pd

from rxradar.ingest.fda_registry import read_tsv
from rxradar.ingest.openfda import load_results
from rxradar.paths import latest_snapshot_dir

SUFFIXES = (
    r"\b(INC|INCORPORATED|LLC|L L C|LTD|LIMITED|CORP|CORPORATION|CO|COMPANY|PLC|SA|AG|GMBH|LP"
    r"|USA|US|PVT|PRIVATE|THE|PHARMACEUTICALS?|PHARMA|LABORATORIES|LABS?|HEALTHCARE|HOLDINGS?)\b"
)


def norm(name: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    s = re.sub(SUFFIXES, " ", s)
    return " ".join(s.split())


def first_token(name: str) -> str:
    n = norm(name)
    return n.split()[0] if n else ""


def main(n: int = 50, seed: int = 7) -> None:
    sh = pd.json_normalize(load_results(latest_snapshot_dir("openfda_shortages")))
    # Labelers as the NDC directory names them, for each shortage NDC.
    ndc_dir = latest_snapshot_dir("fda_ndc_text")
    pkg = pd.concat(
        [read_tsv(ndc_dir / "package.txt"), read_tsv(ndc_dir / "Packages_excluded.txt")]
    )
    prod = pd.concat(
        [read_tsv(ndc_dir / "product.txt"), read_tsv(ndc_dir / "Products_excluded.txt")]
    )
    lab = (
        sh[["package_ndc"]]
        .merge(
            pkg[["NDCPACKAGECODE", "PRODUCTNDC"]], left_on="package_ndc", right_on="NDCPACKAGECODE"
        )
        .merge(prod[["PRODUCTNDC", "LABELERNAME"]].drop_duplicates("PRODUCTNDC"), on="PRODUCTNDC")
    )
    labelers = pd.Series(lab.LABELERNAME.unique())
    sample = labelers.sample(min(n, len(labelers)), random_state=seed).sort_values()

    est = read_tsv(latest_snapshot_dir("fda_establishments") / "drls_reg.txt")
    firm = {}
    for _, r in est.iterrows():
        for col in ("FIRM_NAME", "REGISTRANT_NAME"):
            firm.setdefault(norm(r[col]), set()).add(r["FEI_NUMBER"])
    tokens = {}
    for k, feis in firm.items():
        tokens.setdefault(k.split()[0] if k else "", set()).update(feis)

    rows = []
    for name in sample:
        exact = firm.get(norm(name), set())
        loose = tokens.get(first_token(name), set())
        rows.append(
            {
                "labeler": name,
                "norm": norm(name),
                "exact_feis": len(exact),
                "first_token_feis": len(loose),
            }
        )
    out = pd.DataFrame(rows)
    print(f"distinct shortage labelers: {len(labelers)}; sample: {len(out)}")
    print(f"normalized exact name match: {(out.exact_feis > 0).mean():.0%}")
    print(f"first-token match (loose, ambiguous): {(out.first_token_feis > 0).mean():.0%}")
    print(f"median FEIs per matched labeler: {out[out.exact_feis > 0].exact_feis.median()}")
    print("\nunmatched (exact):")
    print(out[out.exact_feis == 0].to_string(index=False))
    print("\nmatched to >3 FEIs (sites):")
    print(out[out.exact_feis > 3].to_string(index=False))


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
