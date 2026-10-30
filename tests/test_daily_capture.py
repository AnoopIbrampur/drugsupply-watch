"""Daily capture path for CI: bot-protection handling and openFDA listings."""

import json

import httpx
import pandas as pd
import pytest

from rxradar.http import BlockedError, PoliteClient
from rxradar.ingest.openfda import shortage_listing_rows


def test_bot_protection_redirect_is_not_retried():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "apology" in req.url.path:
            return httpx.Response(404)
        return httpx.Response(
            302,
            headers={"Location": "https://x.test/apology_objects/abuse-detection-apology.html"},
        )

    client = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))
    with pytest.raises(BlockedError):
        client.get_bytes("https://x.test/scripts/drugshortages/default.cfm")
    assert calls["n"] == 2  # one redirect followed, no retries


def write_openfda(tmp_path, day, export_date, rows):
    d = tmp_path / "data" / "raw" / "openfda_shortages" / day
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"export_date": export_date}))
    (d / "s.json").write_text(json.dumps({"results": rows}))
    return d


def test_openfda_snapshot_becomes_one_capture(tmp_path):
    rows = [
        {"generic_name": "Drug A  Injection", "status": "Current"},
        {"generic_name": "Drug A Injection", "status": "Current"},  # package-level duplicate
        {"generic_name": "Drug B Tablet", "status": "To Be Discontinued"},
    ]
    d = write_openfda(tmp_path, "2026-10-31", "2026-10-31", rows)
    out = shortage_listing_rows(d)
    assert out == [
        {"capture_ts": "20261031120000", "name": "Drug A Injection", "status": "Current"},
        {"capture_ts": "20261031120000", "name": "Drug B Tablet", "status": "To Be Discontinued"},
    ]


def test_page_capture_wins_on_the_same_day(tmp_path, monkeypatch):
    import gzip

    from rxradar.transform import build

    monkeypatch.setenv("RXRADAR_DATA", str(tmp_path / "data"))
    arch = tmp_path / "data" / "raw" / "fda_shortage_archive" / "2026-10-29"
    arch.mkdir(parents=True)
    page = tmp_path / "data" / "raw" / "fda_shortage_page" / "2026-10-30"
    page.mkdir(parents=True)
    html = '<a href="dsp_ActiveIngredientDetails.cfm?AI=Drug A Injection&st=c&tab=tabs-1">'
    (page / "20261030150000.html.gz").write_bytes(gzip.compress(html.encode()))
    write_openfda(
        tmp_path, "2026-10-30", "2026-10-30", [{"generic_name": "Other", "status": "Current"}]
    )
    write_openfda(
        tmp_path,
        "2026-10-31",
        "2026-10-31",
        [{"generic_name": "Drug A Injection", "status": "Current"}],
    )
    lst = build.build_listings()
    by_day = lst.groupby(lst.capture_ts.str[:8]).name.apply(set).to_dict()
    assert by_day == {"20261030": {"Drug A Injection"}, "20261031": {"Drug A Injection"}}
    # The openFDA table keeps both days for later rebuilds.
    saved = pd.read_parquet(tmp_path / "data" / "derived" / "openfda_listings.parquet")
    assert set(saved.capture_ts.str[:8]) == {"20261030", "20261031"}
