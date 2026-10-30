"""FDA bulk files that describe *who makes what*: NDC text files and drug
establishment registrations.

Why these and not only the openFDA NDC JSON:
- ``ndc_excluded.zip`` holds ~200k products that are no longer listed
  (expired, delisted, inactive), each with STARTMARKETINGDATE and
  ENDMARKETINGDATE. Together with the current ``ndctext.zip`` this lets us
  approximate which labelers sold which products in past years, which the
  current-state openFDA NDC file cannot.
- ``drls_reg.zip`` (Drug Establishments Current Registration Site) gives FEI
  and DUNS numbers with firm names and addresses. FEI is the key FDA uses for
  inspections and compliance actions, so this is our bridge from labeler names
  to inspection outcomes.

All files are fetched from accessdata.fda.gov and saved unzipped as delivered.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

BASE = "https://www.accessdata.fda.gov/cder/"
FILES = {
    "fda_ndc_text": ["ndctext.zip", "ndc_excluded.zip"],
    "fda_establishments": ["drls_reg.zip", "drls_excluded.zip"],
}


def ingest(client: PoliteClient, sources: tuple[str, ...] | None = None) -> list[Path]:
    """Download and unzip the files in FILES (or only ``sources``); keep only
    the .txt members (the .xls copies in the same zips are byte-identical
    tab-separated text)."""
    outs = []
    for source, names in FILES.items():
        if sources is not None and source not in sources:
            continue
        out = raw_snapshot_dir(source)
        for name in names:
            blob = client.get_bytes(BASE + name, use_cache=False)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for member in zf.namelist():
                    if member.lower().endswith(".txt"):
                        (out / Path(member).name).write_bytes(zf.read(member))
            log.info("%s -> %s", name, out)
        outs.append(out)
    return outs


def read_tsv(path: Path):
    """Read one of FDA's tab-separated text files as strings.

    These files are latin-1 encoded, and the registration file pads every
    value with spaces and ends each row with an extra tab, so we strip all
    cells and pass ``index_col=False`` to stop pandas from shifting the first
    column into the index.
    """
    import pandas as pd

    df = pd.read_csv(
        path, sep="\t", dtype=str, encoding="latin-1", keep_default_na=False, index_col=False
    )
    df.columns = [c.strip() for c in df.columns]
    return df.apply(lambda col: col.str.strip())
