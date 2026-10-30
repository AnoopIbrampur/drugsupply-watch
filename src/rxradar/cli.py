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
            "warning_letters",
            "nadac",
            "dashboard",
        ],
    )
    sub.add_parser("build", help="rebuild derived tables (families, labels, panel)")
    sub.add_parser("train", help="fit the production model (data/derived/model.json)")
    sub.add_parser("score", help="score the latest week (data/site_data/scores.json)")
    sub.add_parser("site", help="build the static site into public/")
    sub.add_parser("nightly", help="ingest small sources, rebuild, score, build site")
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
    elif args.cmd == "nightly":
        from rxradar.pipeline import nightly

        nightly()
    elif args.cmd == "state":
        from pathlib import Path

        from rxradar.pipeline import pack_state, unpack_state

        (pack_state if args.action == "pack" else unpack_state)(Path(args.path))


if __name__ == "__main__":
    main()
