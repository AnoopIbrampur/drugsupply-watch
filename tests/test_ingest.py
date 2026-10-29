"""Ingestion tests. All HTTP is served by httpx.MockTransport from recorded
fixtures in tests/fixtures/, so CI never touches the network."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from rxradar.http import PoliteClient
from rxradar.ingest import fda_shortage_archive as archive
from rxradar.ingest import openfda

FIXTURES = Path(__file__).parent / "fixtures"


def make_client(handler, **kw) -> PoliteClient:
    return PoliteClient(min_interval=0, transport=httpx.MockTransport(handler), **kw)


def zipped(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, payload)
    return buf.getvalue()


def test_openfda_bulk_snapshot_roundtrip(tmp_path):
    payload = (FIXTURES / "openfda_shortages_sample.json").read_bytes()
    manifest = {
        "results": {
            "drug": {
                "shortages": {
                    "export_date": "2026-10-29",
                    "total_records": 3,
                    "partitions": [{"file": "https://download.open.fda.gov/s.json.zip"}],
                }
            }
        }
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/download.json":
            return httpx.Response(200, json=manifest)
        return httpx.Response(200, content=zipped("s.json", payload))

    out = openfda.ingest_dataset(make_client(handler), "shortages", out_dir=tmp_path)
    records = openfda.load_results(out)
    assert {r["status"] for r in records} == {"Resolved", "Current", "To Be Discontinued"}
    saved = json.loads((out / "manifest.json").read_text())
    assert saved["export_date"] == "2026-10-29" and saved["records_in_files"] == 3


def test_client_retries_transient_errors(monkeypatch):
    monkeypatch.setattr("rxradar.http.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] < 3 else 200, content=b"ok")

    assert make_client(handler).get_bytes("https://x.test/a") == b"ok"
    assert calls["n"] == 3


def test_client_raises_on_permanent_error():
    client = make_client(lambda req: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        client.get_bytes("https://x.test/missing")


def test_client_disk_cache_avoids_second_request(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, content=b"capture")

    client = make_client(handler, cache_dir=tmp_path)
    client.get_bytes("https://x.test/c")
    client.get_bytes("https://x.test/c")
    assert calls["n"] == 1


def test_parse_listing_on_recorded_2014_page():
    rows = archive.parse_listing((FIXTURES / "fda_shortage_page_2014.html").read_text())
    names = {r.name for r in rows}
    assert "Acetylcysteine Inhalation Solution" in names
    assert all(r.status in archive.STATUS_CODES.values() for r in rows)


def test_parse_listing_dedupes_and_decodes():
    html = (
        '<a href="dsp_ActiveIngredientDetails.cfm?AI=Digoxin%20Injection&st=c&tab=tabs-1">'
        '<a href="dsp_ActiveIngredientDetails.cfm?AI=Digoxin+Injection&amp;st=c&tab=tabs-3&i=4">'
        '<a href="dsp_ActiveIngredientDetails.cfm?AI=Famotidine Injection&st=r&tab=tabs-1">'
    )
    rows = archive.parse_listing(html)
    assert rows == [
        archive.ListingRow("Digoxin Injection", "Current"),
        archive.ListingRow("Famotidine Injection", "Resolved"),
    ]


def test_weekly_keeps_first_capture_per_iso_week():
    ts = ["20230102120000", "20230104120000", "20230109000000", "20230101000000"]
    # 2023-01-01 is a Sunday (ISO week 52 of 2022); Jan 2 and 4 share week 1.
    assert archive.weekly(ts) == ["20230101000000", "20230102120000", "20230109000000"]


def test_archive_ingest_and_load(tmp_path):
    page = (FIXTURES / "fda_shortage_page_2014.html").read_bytes()
    cdx = [["timestamp", "statuscode"], ["20140623000000", "200"], ["20140625120000", "200"]]

    def handler(req):
        if req.url.path.startswith("/cdx"):
            return httpx.Response(200, json=cdx)
        return httpx.Response(200, content=page)

    out = archive.ingest(make_client(handler), out_dir=tmp_path)
    files = sorted(p.name for p in out.glob("*.html.gz"))
    assert files == ["20140623000000.html.gz"]  # same ISO week -> one capture
    assert gzip.decompress((out / files[0]).read_bytes()) == page
    rows = archive.load_listings(out)
    assert rows and all(r["capture_ts"] == "20140623000000" for r in rows)
