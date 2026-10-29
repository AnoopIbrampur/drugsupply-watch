"""Command-line entry point: ``rxradar ingest <source|all>``."""

from __future__ import annotations

import argparse
import logging
import os

from rxradar.http import PoliteClient
from rxradar.paths import cache_dir


def _openfda_client() -> PoliteClient:
    # openFDA allows 240 requests/min; bulk files need only a handful of calls.
    return PoliteClient(min_interval=0.3)


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
    if source in ("all", "archive"):
        fda_shortage_archive.ingest(_archive_client())
    if source in ("all", "registry"):
        from rxradar.ingest import fda_registry

        fda_registry.ingest(_openfda_client())
    if source in ("all", "inspections"):
        from rxradar.ingest import fda_inspections

        fda_inspections.ingest(_openfda_client())
    if source in ("all", "nadac"):
        from rxradar.ingest import nadac

        nadac.ingest(_openfda_client())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rxradar")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ingest = sub.add_parser("ingest", help="download raw snapshots")
    p_ingest.add_argument(
        "source", choices=["all", "openfda", "archive", "registry", "inspections", "nadac"]
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s"
    )
    if args.cmd == "ingest":
        ingest(args.source)


if __name__ == "__main__":
    main()
