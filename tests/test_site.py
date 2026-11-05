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
        "group": "Infections",
        "context": "Sterile injectables are in shortage more often than other drugs.",
        "peer_note": None,
        "n_generic_labelers": 2,
        "lost_companies": [],
    }


def empty_changes():
    return {"new_shortages": [], "resolved_shortages": [], "moved_up_band": [], "big_jumps": []}


def scores(tmp_path, changes=None):
    data = {
        "generated_at": "2026-10-30T00:00:00+00:00",
        "week": "2026-10-26",
        "model": {"type": "calibrated logistic regression", "trained_through": "2026-10-30"},
        "base_rate": 0.0025,
        "freshness": {"FDA shortage list": "2026-10-30"},
        "changes": changes or empty_changes(),
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
    pages = [
        out / "index.html",
        out / "about.html",
        out / "shortages.html",
        out / "track-record.html",
        out / "all.html",
        out / "fragile.html",
    ]
    pages += sorted((out / "drug").glob("*.html"))
    assert len(pages) == 9
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


def test_feed_lists_only_real_changes(tmp_path):
    changes = empty_changes()
    item = {"name": "Evil", "slug": "evil", "form": "injection", "rank": 1}
    changes["moved_up_band"] = [{**item, "from": "typical", "to": "high"}]
    out = build_site(scores(tmp_path, changes), tmp_path / "public")
    root = ET.parse(out / "feed.xml").getroot()
    titles = [i.findtext("title") for i in root.iter("item")]
    assert len(titles) == 1 and "moved from typical to high" in titles[0]


def test_feed_is_empty_on_a_quiet_week(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    assert not list(ET.parse(out / "feed.xml").getroot().iter("item"))


def test_quiet_week_says_so_and_news_is_listed(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    assert "No notable changes this week" in (out / "index.html").read_text()
    changes = empty_changes()
    changes["new_shortages"] = [{"name": "Newly Short", "slug": "evil", "form": "tablet"}]
    out = build_site(scores(tmp_path, changes), tmp_path / "public2")
    assert "New FDA-listed shortages" in (out / "index.html").read_text()


def test_fragile_page_is_facts_and_filters_brands(tmp_path):
    data = json.loads(scores(tmp_path).read_text())
    data["families"][0].update(n_labelers=1, n_generic_labelers=0)  # brand-only: excluded
    data["families"][1].update(n_labelers=1, n_generic_labelers=1, group="Heart")
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data))
    html = (build_site(p, tmp_path / "public") / "fragile.html").read_text()
    assert "Steady Tablet" in html and "alert(1)" not in html
    assert "mean a shortage is coming" in html


def test_drug_page_explains_when_there_are_no_specific_reasons(tmp_path):
    data = json.loads(scores(tmp_path).read_text())
    data["families"][0].update(reasons=[], lost_companies=["Old Co"], peer_note="More recalls.")
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data))
    html = (build_site(p, tmp_path / "public") / "drug" / "evil.html").read_text()
    assert "reflects its drug type" in html
    assert "Old Co" in html and "More recalls." in html


def test_search_index(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    idx = json.loads((out / "search.json").read_text())
    assert {e["s"] for e in idx} == {"evil", "steady", "short"}
    evil = next(e for e in idx if e["s"] == "evil")
    assert evil["g"] == "Infections" and evil["f"] == "Injection" and evil["d"] == "high"


def track_record(**over):
    summary = {
        "precision@50": None,
        "rule_precision@50": None,
        "precision@100": None,
        "rule_precision@100": None,
        "random_precision": None,
        "recall@50": 0.5,
        "rule_recall@50": 0.0,
        "random_recall@50": 0.015,
        "recall@100": 1.0,
        "rule_recall@100": 0.5,
        "random_recall@100": 0.03,
    }
    record = {
        "started": "2026-11-03",
        "weeks_frozen": 12,
        "weeks_pending": 12,
        "weeks_scored": 0,
        "weeks_unknown": 0,
        "onsets_scored": 2,
        "onsets_too_soon": 1,
        "summary": summary,
        "onsets": [
            {
                "start": "2027-01-01",
                "name": "<b>Drug</b>",
                "rank": 7,
                "lead_days": 40,
                "hit@100": True,
            },
            {
                "start": "2027-01-08",
                "name": "Missed",
                "rank": None,
                "lead_days": 35,
                "hit@100": False,
            },
        ],
        "weeks": [],
        "bands": None,
    }
    return {**record, **over}


def test_track_page_shows_misses_and_warns_when_early(tmp_path):
    (tmp_path / "track_record.json").write_text(json.dumps(track_record()))
    html = (build_site(scores(tmp_path), tmp_path / "public") / "track-record.html").read_text()
    assert "Too early to judge" in html
    assert "missed" in html and "caught (top 100)" in html
    assert "&lt;b&gt;Drug&lt;/b&gt;" in html  # names are escaped
    assert "predicted vs actual" not in html.lower()  # bands wait for 30 onsets


def test_track_page_before_tracking_starts(tmp_path):
    html = (build_site(scores(tmp_path), tmp_path / "public") / "track-record.html").read_text()
    assert "hasn't started" in html


def test_lost_company_section_only_counts_when_few_remain(tmp_path):
    from rxradar.site.build import lost_a_company

    base = {"lost_companies": ["Gone"], "n_generic_labelers": 2, "injectable": False}
    assert lost_a_company({**base, "n_labelers": 3})
    assert not lost_a_company({**base, "n_labelers": 12})
    assert not lost_a_company({**base, "n_labelers": 2, "lost_companies": []})


def test_inspection_section_keeps_unknown_apart_from_clean():
    from rxradar.site.build import inspection_section

    assert "doesn't mean the record is clean" in inspection_section(None)
    text = inspection_section({"factories": 1, "inspected": 1, "failed_3y": 1, "actions_3y": 0})
    assert "1 factory" in text
    assert "isn't\na current safety warning" in text


def test_site_loads_nothing_from_other_hosts(tmp_path):
    out = build_site(scores(tmp_path), tmp_path / "public")
    assert (out / "fonts" / "public-sans-latin-wght-normal.woff2").exists()
    for name in ("index.html", "style.css", "drug/evil.html"):
        text = (out / name).read_text()
        assert "googleapis" not in text and "gstatic" not in text


def test_chart_axis_is_round_and_a_missing_week_is_a_gap():
    from rxradar.site.build import axis_ticks, risk_chart

    assert axis_ticks(0.01)[0][-1] >= 0.01 and axis_ticks(0.01)[1] == 1
    ticks, decimals = axis_ticks(0.0787)
    assert ticks[-1] >= 0.0787 and len(ticks) <= 6 and decimals == 0
    week = [{"week": f"2026-01-{d:02d}", "short": False} for d in (5, 12, 19, 26)]
    probs = [0.01, None, 0.02, 0.03]
    svg = risk_chart([{**w, "prob": p} for w, p in zip(week, probs, strict=True)])
    path = svg.split('<path d="')[1].split('"')[0]
    assert path.count("M") == 2 and path.count("L") == 1  # the line lifts over the gap
