"""Label history: DUNS parsing, version choice, resumable runs."""

import io
import json
import zipfile

import httpx
import pandas as pd
import pytest

from rxradar.http import PoliteClient
from rxradar.ingest import dailymed

XML = """<document>
<id root="1.3.6.1.4.1.519.1" extension="080548348"/><name>Labeler</name>
<id extension="529111185" root="1.3.6.1.4.1.519.1"/><name>Factory</name>
<id root="1.3.6.1.4.1.519.1" extension="529111185"/><name>Factory</name>
<id root="other" extension="123456789"/>
</document>"""
HISTORY = [
    {"spl_version": 3, "published_date": "Jul 28, 2025"},
    {"spl_version": 2, "published_date": "Nov 06, 2020"},
    {"spl_version": 1, "published_date": "Mar 10, 2018"},
]


def test_establishment_duns_skips_labeler_and_handles_attribute_order():
    assert dailymed.establishment_duns(XML) == ["529111185"]


def test_versions_are_the_one_in_effect_each_january_plus_latest():
    out = dailymed.versions_to_read(HISTORY, 2026)
    assert [v["version"] for v in out] == [1, 2, 3]
    assert out[2]["published"] == "2025-07-28"
    # Version 3 wasn't public on 2025-01-01, so a 2025 run still needs it as latest.
    assert [v["version"] for v in dailymed.versions_to_read(HISTORY[1:], 2019)] == [1, 2]


def test_selection_is_capped_and_prefers_larger_labelers():
    p = pd.DataFrame(
        {
            "family": ["F"] * 8,
            "LABELERNAME": ["Big", "Big", "A", "B", "C", "D", "E", "G"],
            "PRODUCTNDC": [str(i) for i in range(8)],
        }
    )
    out = dailymed.select_products(p, ["F"])
    assert len(out) == dailymed.LABELS_PER_FAMILY and out.iloc[0].LABELERNAME == "Big"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RXRADAR_DATA", str(tmp_path))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("label.xml", XML)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("spls.json"):
            return httpx.Response(200, json={"data": [{"setid": "S1"}]})
        if req.url.path.endswith("history.json"):
            return httpx.Response(200, json={"data": {"history": HISTORY}})
        return httpx.Response(200, content=buf.getvalue())

    c = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))
    c.calls = calls
    return c


SELECTED = pd.DataFrame({"family": ["F"], "PRODUCTNDC": ["0001-0001"], "LABELERNAME": ["L"]})


def test_ingest_writes_rows_and_a_second_run_does_no_work(client):
    dailymed.ingest(client, SELECTED, 2026)
    first = len(client.calls)
    assert first == 5  # search, history, three versions
    rows = (dailymed.output_dir() / "versions.jsonl").read_text().splitlines()
    assert json.loads(rows[0])["duns"] == ["529111185"]
    dailymed.ingest(client, SELECTED, 2026)
    assert len(client.calls) == first


def test_interrupted_label_is_redone_without_refetching_finished_versions(client):
    dailymed.ingest(client, SELECTED, 2026)
    (dailymed.output_dir() / "labels.jsonl").unlink()
    client.calls.clear()
    dailymed.ingest(client, SELECTED, 2026)
    assert len(client.calls) == 2  # only search + history
