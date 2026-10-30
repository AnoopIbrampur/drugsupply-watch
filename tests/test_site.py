"""Static site build on a tiny synthetic scores.json."""

import json
import xml.etree.ElementTree as ET

from rxradar.site.build import build_site

HISTORY = [
    {"week": "2026-10-19", "prob": 0.004, "short": False},
    {"week": "2026-10-26", "prob": 0.03, "short": False},
]


def entry(slug, name, rank, prob, band, prev_rank=None, short=False, family=None, history=None):
    return {
        "family": family or f"{slug}|INJECTION|PARENTERAL",
        "slug": slug,
        "name": name,
        "form": "injection",
        "in_shortage_now": short,
        "prob": None if short else prob,
        "band": None if short else band,
        "multiple": None if short else round(prob / 0.0025, 1),
        "rank": rank,
        "prev_rank": prev_rank,
        "reasons": [] if short else ["2 FDA-listed shortages since 2014"],
        "n_labelers": 2,
        "injectable": True,
        "alternatives": {"labelers": ["Maker A", "Maker B"], "other_forms": []},
        "history": history or HISTORY,
    }


def scores(tmp_path):
    data = {
        "generated_at": "2026-10-30T00:00:00+00:00",
        "week": "2026-10-26",
        "model": {"type": "calibrated logistic regression", "trained_through": "2026-10-30"},
        "base_rate": 0.0025,
        "freshness": {"FDA shortage list": "2026-10-30"},
        "families": [
            entry("evil", "<script>alert(1)</script> Injection", 1, 0.03, "high", prev_rank=None),
            entry(
                "steady",
                "Steady Tablet",
                2,
                0.006,
                "moderate",
                prev_rank=2,
                history=[
                    {"week": "2026-10-19", "prob": 0.006, "short": False},
                    {"week": "2026-10-26", "prob": 0.006, "short": False},
                ],
            ),
            entry("short", "Short Injection", None, None, None, short=True),
        ],
    }
    p = tmp_path / "scores.json"
    p.write_text(json.dumps(data))
    return p


def test_pages_exist_and_carry_the_disclaimer(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    pages = [out / "index.html", out / "about.html", out / "shortages.html"]
    pages += sorted((out / "drug").glob("*.html"))
    assert len(pages) == 6
    for p in pages:
        assert "not clinical advice" in p.read_text()


def test_names_are_escaped(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    index = (out / "index.html").read_text()
    assert "<script>alert(1)</script>" not in index
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in index


def test_shortage_families_are_not_ranked(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    index = (out / "index.html").read_text()
    assert "Short Injection" not in index
    assert "Short Injection" in (out / "shortages.html").read_text()
    assert "not ranked" in (out / "drug" / "short.html").read_text()


def test_feed_is_valid_rss_with_only_real_changes(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    root = ET.parse(out / "feed.xml").getroot()
    titles = [i.findtext("title") for i in root.iter("item")]
    # "evil" is new to the top 50; "steady" didn't move up a band.
    assert len(titles) == 1 and "entered the top 50" in titles[0]


def test_search_index(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    idx = json.loads((out / "search.json").read_text())
    assert {e["s"] for e in idx} == {"evil", "steady", "short"}
