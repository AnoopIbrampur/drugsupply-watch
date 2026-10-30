"""Nightly pipeline and the saved "state" it runs on.

GitHub Actions runners start empty, and the full raw history is ~5 GB. So
the nightly job works from a compact *state* (~100 MB): derived tables, the
Wayback shortage-list captures, our own daily captures, the substance lookup
tables and the model. It is stored as a GitHub Release asset and is
downloaded, updated and re-uploaded on every run.

Nightly (``nightly``):
1. Fetch the small, fast-changing sources: openFDA (shortages, NDC, recalls,
   Drugs@FDA) and FDA's NDC text files. FDA's shortage page is not fetched
   here: it blocks cloud servers, and openFDA's daily shortage export carries
   the same list.
2. Mondays: refresh the current year's NADAC prices.
3. Rebuild derived tables, panel and features (NDC snapshots extend
   incrementally; older years' prices are kept).
4. On the 1st of the month: retrain the production model.
5. Score the latest week and build the site into public/.
"""

from __future__ import annotations

import datetime as dt
import logging
import tarfile
from pathlib import Path

import pandas as pd

from rxradar.paths import data_root, derived_dir

log = logging.getLogger(__name__)

# Paths (relative to the data root) that make up the state.
STATE_PATHS = [
    "derived",
    "raw/fda_shortage_archive",
    "raw/fda_shortage_page",
    "raw/openfda_substance",
]
STATE_EXCLUDE_SUFFIXES = (".json",)  # the 2.4 GB raw substance JSON stays out


def _in_state(path: Path) -> bool:
    rel = path.relative_to(data_root()).as_posix()
    if not any(rel == p or rel.startswith(p + "/") for p in STATE_PATHS):
        return False
    return not (rel.startswith("raw/openfda_substance") and path.suffix in STATE_EXCLUDE_SUFFIXES)


def pack_state(out: Path) -> Path:
    with tarfile.open(out, "w:gz") as tar:
        for path in sorted(data_root().rglob("*")):
            if path.is_file() and _in_state(path):
                tar.add(path, arcname=path.relative_to(data_root()).as_posix())
    log.info("state packed: %s (%.1f MB)", out, out.stat().st_size / 1e6)
    return out


def unpack_state(archive: Path) -> None:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(root, filter="data")  # "data" filter blocks path traversal


def nightly(today: dt.date | None = None) -> None:
    from rxradar.cli import ingest
    from rxradar.features.build import build_features
    from rxradar.model.score import score, train
    from rxradar.site.build import build_site
    from rxradar.transform.build import build_all

    today = today or dt.date.today()
    failed = []
    # A source being down must not stop the run: we continue on the last good
    # data, and the site's "Data through" dates show any staleness.
    # FDA's own shortage page and establishment files sit behind bot
    # protection that blocks cloud runners (2026-10-30); the nightly job uses
    # openFDA's daily shortage export instead (see transform/build.py).
    for source in ("openfda", "ndc_text"):
        try:
            ingest(source)
        except Exception:
            log.exception("ingest %s failed; continuing with previous data", source)
            failed.append(source)
    if today.weekday() == 0:
        try:
            refresh_prices(today.year)
        except Exception:
            log.exception("NADAC refresh failed; keeping previous prices")
            failed.append("nadac")
    build_all(features=False)
    build_features()
    if today.day == 1 or not (derived_dir() / "model.json").exists():
        train(pd.Timestamp(today))
    score()
    build_site()
    if failed:
        log.warning("nightly finished with stale sources: %s", ", ".join(failed))


def refresh_prices(year: int) -> None:
    """Download this year's NADAC file and splice it into the price table."""
    import glob

    from rxradar.cli import _openfda_client
    from rxradar.features import prices
    from rxradar.ingest import nadac

    nadac.ingest(_openfda_client(), years=[year])
    files = sorted(glob.glob(str(data_root() / "raw" / "nadac" / "*" / f"nadac_{year}.csv")))
    new = prices.price_source(files[-1:], prices.package_family_map())
    path = derived_dir() / "prices_family_weekly.parquet"
    prices.update_price_table(pd.read_parquet(path), new).to_parquet(path)
