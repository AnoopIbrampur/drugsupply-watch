"""Nightly label refresh: adds newer versions, rotates, survives a bad label."""

import pandas as pd

from rxradar.ingest import dailymed
from rxradar.transform import label_factories as lf


def seed(path):
    table = pd.DataFrame(
        {
            "family": ["F", "F"],
            "set_id": ["a", "b"],
            "version": [1, 1],
            "published": pd.to_datetime(["2020-01-01", "2020-01-01"]).astype("datetime64[ns]"),
            "fei": ["1", "2"],
        }
    )
    table.to_parquet(path / "label_factories.parquet")
    pd.DataFrame({"duns": ["111"], "fei": ["9"]}).to_parquet(path / "duns_fei.parquet")


def test_refresh_adds_new_version_and_rotates(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr(lf, "derived_dir", lambda: tmp_path)

    def latest(client, set_id, known):
        if set_id == "b":
            raise OSError("down")
        return {"version": 2, "published": "2024-01-01", "duns": ["111"]}

    monkeypatch.setattr(lf, "latest_version", latest)
    assert lf.refresh_batch(None, n=1) == 1
    out = pd.read_parquet(tmp_path / "label_factories.parquet")
    new = out[out.version == 2].iloc[0]
    assert (new.family, new.set_id, new.fei) == ("F", "a", "9")
    assert (tmp_path / "label_refresh_cursor.txt").read_text() == "1"
    assert lf.refresh_batch(None, n=1) == 0  # the bad label doesn't stop the run


def test_latest_version_skips_known_versions(monkeypatch):
    class Client:
        def get_json(self, url):
            history = [{"spl_version": 3, "published_date": "Jan 05, 2024"}]
            return {"data": {"history": history}}

    monkeypatch.setattr(dailymed, "_label_xml", lambda c, s, v: "")
    assert dailymed.latest_version(Client(), "a", 3) is None
    assert dailymed.latest_version(Client(), "a", 2)["published"] == "2024-01-05"
