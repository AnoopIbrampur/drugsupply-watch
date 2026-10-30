"""State archive: includes what the nightly job needs, nothing else."""

import tarfile

import pytest

from rxradar.pipeline import pack_state, unpack_state


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("RXRADAR_DATA", str(data))
    for rel in [
        "derived/panel.parquet",
        "raw/fda_shortage_archive/2026-10-29/20140622.html.gz",
        "raw/fda_shortage_page/2026-10-30/20261030.html.gz",
        "raw/openfda_substance/2026-10-29/_substance_names.parquet",
        "raw/openfda_substance/2026-10-29/other-substance-0001-of-0001.json",  # 2.4 GB in reality
        "raw/nadac/2026-10-29/nadac_2014.csv",  # raw history stays out
    ]:
        p = data / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rel)
    return data


def test_pack_includes_state_only(root, tmp_path):
    archive = pack_state(tmp_path / "state.tar.gz")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert "derived/panel.parquet" in names
    assert "raw/fda_shortage_page/2026-10-30/20261030.html.gz" in names
    assert "raw/openfda_substance/2026-10-29/_substance_names.parquet" in names
    assert not any(n.endswith(".json") for n in names)
    assert not any(n.startswith("raw/nadac") for n in names)


def test_unpack_roundtrip(root, tmp_path, monkeypatch):
    archive = pack_state(tmp_path / "state.tar.gz")
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("RXRADAR_DATA", str(fresh))
    unpack_state(archive)
    assert (fresh / "derived/panel.parquet").read_text() == "derived/panel.parquet"
