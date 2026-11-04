"""Command-line entry point: ``rxradar ingest <source|all>``."""

from __future__ import annotations

import argparse
import logging
import os

from rxradar.http import PoliteClient
from rxradar.paths import cache_dir


def _openfda_client() -> PoliteClient:
    # Used for openFDA and other .gov bulk files: a handful of large downloads,
    # so a short gap is plenty (openFDA allows 240 requests/min).
    return PoliteClient(min_interval=0.3, timeout=600)


def _archive_client() -> PoliteClient:
    # The Internet Archive is a non-profit; stay well under its limits and
    # cache captures forever (they are immutable).
    return PoliteClient(min_interval=1.5, cache_dir=cache_dir() / "wayback")


def ingest(source: str) -> None:
    from rxradar.ingest import fda_shortage_archive, openfda

    if source in ("all", "openfda"):
        client = _openfda_client()
        for dataset in openfda.DATASETS:
            openfda.ingest_dataset(client, dataset)
    if source == "substance":  # reference data, not part of "all"
        openfda.ingest_dataset(_openfda_client(), "substance")
    if source in ("all", "page"):
        from rxradar.ingest import fda_shortage_page

        fda_shortage_page.ingest(_openfda_client())
    if source in ("all", "archive"):
        fda_shortage_archive.ingest(_archive_client())
    if source in ("all", "ndc_archive"):
        from rxradar.ingest import ndc_archive

        ndc_archive.ingest(_archive_client())
    if source in ("all", "registry"):
        from rxradar.ingest import fda_registry

        fda_registry.ingest(_openfda_client())
    if source == "ndc_text":  # NDC files only (nightly); establishments aren't modelled
        from rxradar.ingest import fda_registry

        fda_registry.ingest(_openfda_client(), sources=("fda_ndc_text",))
    if source in ("all", "warning_letters"):
        from rxradar.ingest import warning_letters

        warning_letters.ingest(_openfda_client())
    if source in ("all", "nadac"):
        from rxradar.ingest import nadac

        nadac.ingest(_openfda_client())
    if source in ("all", "dashboard"):
        from rxradar.ingest import fda_dashboard

        try:
            fda_dashboard.ingest()
        except fda_dashboard.MissingCredentials as exc:
            # Not fatal for "all": every other source still updates.
            logging.getLogger(__name__).warning("skipping FDA Data Dashboard: %s", exc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rxradar")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ingest = sub.add_parser("ingest", help="download raw snapshots")
    p_ingest.add_argument(
        "source",
        choices=[
            "all",
            "openfda",
            "substance",
            "page",
            "archive",
            "ndc_archive",
            "registry",
            "ndc_text",
            "warning_letters",
            "nadac",
            "dashboard",
        ],
    )
    sub.add_parser("build", help="rebuild derived tables (families, labels, panel)")
    sub.add_parser("train", help="fit the production model (data/derived/model.json)")
    sub.add_parser("score", help="score the latest week (data/site_data/scores.json)")
    sub.add_parser("site", help="build the static site into public/")
    sub.add_parser("track", help="freeze this week if new, rescore the track record")
    sub.add_parser("nightly", help="ingest small sources, rebuild, score, build site")
    sub.add_parser("dashboard-check", help="test FDA Data Dashboard credentials")
    sub.add_parser("dailymed", help="fetch label version history (slow, resumable)")
    p_state = sub.add_parser("state", help="pack/unpack the compact state archive")
    p_state.add_argument("action", choices=["pack", "unpack"])
    p_state.add_argument("path")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s"
    )
    if args.cmd == "ingest":
        ingest(args.source)
    elif args.cmd == "build":
        from rxradar.transform.build import build_all

        build_all()
    elif args.cmd == "train":
        from rxradar.model.score import train

        train()
    elif args.cmd == "score":
        from rxradar.model.score import score

        score()
    elif args.cmd == "site":
        from rxradar.site.build import build_site

        build_site()
    elif args.cmd == "track":
        import json

        from rxradar.model.score import site_data_dir
        from rxradar.track import update_track_record

        scores = json.loads((site_data_dir() / "scores.json").read_text())
        update_track_record(scores, site_data_dir())
    elif args.cmd == "dailymed":
        import datetime as dt

        import pandas as pd

        from rxradar.ingest import dailymed
        from rxradar.paths import derived_dir

        panel = pd.read_parquet(derived_dir() / "panel.parquet")
        latest = panel[panel.week == panel.week.max()]
        families = latest[latest.multisource_generic | latest.injectable].family.tolist()
        products = pd.read_parquet(derived_dir() / "product_families.parquet")
        selected = dailymed.select_products(products[products.listing == "current"], families)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        client = PoliteClient(min_interval=1.0)
        dailymed.ingest(client, selected, dt.date.today().year)
    elif args.cmd == "dashboard-check":
        import json

        from rxradar.ingest import fda_dashboard

        report = fda_dashboard.check_connection()
        print(json.dumps(report, indent=2))
        ok = all(r["statuscode"] == fda_dashboard.STATUS_SUCCESS for r in report.values())
        raise SystemExit(0 if ok else 1)
    elif args.cmd == "nightly":
        from rxradar.pipeline import nightly

        nightly()
    elif args.cmd == "state":
        from pathlib import Path

        from rxradar.pipeline import pack_state, unpack_state

        (pack_state if args.action == "pack" else unpack_state)(Path(args.path))


if __name__ == "__main__":
    main()
