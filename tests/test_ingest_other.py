"""Tests for the registry, NADAC and warning-letter fetchers, served from
in-memory fixtures (no network)."""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from rxradar.http import PoliteClient
from rxradar.ingest import fda_registry, nadac, warning_letters

REG_HEADER = "FEI_NUMBER\tDUNS_NUMBER\tFIRM_NAME\tEXCLUSION_FLAG\n"
REG_ROW = "0000000405 \t 986154341 \t VANTIVE MANUFACTURING LIMITED \t N \t\n"


def make_client(handler) -> PoliteClient:
    return PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RXRADAR_DATA", str(tmp_path))
    return tmp_path


def zip_of(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_registry_keeps_txt_and_strips_padding():
    def handler(req):
        return httpx.Response(
            200, content=zip_of({"x.txt": REG_HEADER + REG_ROW, "x.xls": REG_HEADER + REG_ROW})
        )

    outs = fda_registry.ingest(make_client(handler))
    assert all(sorted(p.name for p in out.iterdir()) == ["x.txt"] for out in outs)
    df = fda_registry.read_tsv(outs[1] / "x.txt")
    assert df.loc[0, "FEI_NUMBER"] == "0000000405"
    assert df.loc[0, "FIRM_NAME"] == "VANTIVE MANUFACTURING LIMITED"


METASTORE = [
    {
        "title": "NADAC (National Average Drug Acquisition Cost) 2013",
        "distribution": [{"downloadURL": "https://dl.test/n2013.csv"}],
    },
    {
        "title": "NADAC (National Average Drug Acquisition Cost) 2026",
        "distribution": [{"downloadURL": "https://dl.test/n2026.csv"}],
    },
    {"title": "NADAC Comparison", "distribution": [{"downloadURL": "https://dl.test/cmp.csv"}]},
]
CSV = (
    "NDC Description,NDC,NADAC_Per_Unit,Effective_Date,As of Date\n"
    "X,00555071558,0.6,11/21/2013,11/28/2013\n"
)


def test_nadac_lists_only_yearly_files():
    client = make_client(lambda req: httpx.Response(200, json=METASTORE))
    assert nadac.list_year_files(client) == {
        2013: "https://dl.test/n2013.csv",
        2026: "https://dl.test/n2026.csv",
    }


def test_nadac_skips_past_years_already_downloaded(data_root):
    fetched = []

    def handler(req):
        if req.url.host == "data.medicaid.gov":
            return httpx.Response(200, json=METASTORE)
        fetched.append(req.url.path)
        return httpx.Response(200, text=CSV)

    old = data_root / "raw" / "nadac" / "2026-01-01"
    old.mkdir(parents=True)
    (old / "nadac_2013.csv").write_text(CSV)
    nadac.ingest(make_client(handler))
    assert fetched == ["/n2026.csv"]  # 2013 is immutable and already on disk


def test_warning_letters_rejects_html_error_page():
    client = make_client(lambda req: httpx.Response(200, text="<html>error</html>"))
    with pytest.raises(RuntimeError):
        warning_letters.ingest(client)


def test_warning_letters_saves_xlsx():
    client = make_client(lambda req: httpx.Response(200, content=b"PK\x03\x04fake"))
    out = warning_letters.ingest(client)
    assert (out / "warning_letters.xlsx").read_bytes().startswith(b"PK")
