"""Build the static site from data/site_data/scores.json into public/.

Plain HTML + one stylesheet; the only JavaScript is the search box on the
home page. Every value is HTML-escaped. Pages:
- index.html: top-50 watchlist, band legend, search over every family;
- drug/<slug>.html: one page per family (risk, reasons, 2-year history
  sparkline, makers and other forms of the same ingredient);
- all.html: search and filter every family;
- fragile.html: drugs with few listed companies, or that recently lost one;
- shortages.html: families on FDA's shortage list now (not ranked);
- track-record.html: live results from frozen predictions;
- about.html: method, backtest results, limits, sources;
- feed.xml: RSS for risk-band rises and big rank jumps.

Every page says: supply risk only, not clinical advice, and most flagged
drugs will not go short.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import shutil
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from rxradar.model.production import BANDS, BASE_RATE
from rxradar.track import KS, MIN_ONSETS_FOR_BANDS

WATCHLIST_SIZE = 50
FRAGILE_LIMIT = 300
LOST_COMPANY_MAX_LEFT = 3
FORM_FILTERS = ("Injection", "Tablet", "Capsule", "Liquid", "Other")
REPO_URL = "https://github.com/AnoopIbrampur/rxradar"
SITE_TITLE = "RxRadar"
DISCLAIMER = (
    "RxRadar estimates <strong>supply risk</strong> from public FDA and Medicaid data. "
    "It is <strong>not clinical advice</strong>: do not change any treatment based on it. "
    "Most drugs on the watchlist will <em>not</em> go into shortage. In testing, about 1 in 50 "
    "flagged drug-weeks was followed by a new FDA-listed shortage within 90 days."
)
BAND_TEXT = {
    "high": "8× or more the typical risk",
    "elevated": "4–8× the typical risk",
    "moderate": "2–4× the typical risk",
    "typical": "under 2× the typical risk",
}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def pct(p: float | None) -> str:
    if p is None:
        return "—"
    return f"{p * 100:.1f}%" if p >= 0.001 else "<0.1%"


def page(title: str, body: str, root: str = "", freshness: dict | None = None) -> str:
    fresh = ""
    if freshness:
        items = ", ".join(f"{esc(k)} {esc(v)}" for k, v in freshness.items())
        fresh = f"<p class='fresh'>Data through: {items}.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{root}style.css">
<link rel="alternate" type="application/rss+xml" title="RxRadar watchlist" href="{root}feed.xml">
</head><body>
<header><a class="brand" href="{root}index.html">{SITE_TITLE}</a>
<nav><a href="{root}index.html">Watchlist</a><a href="{root}all.html">All drugs</a>
<a href="{root}fragile.html">Fragile supply</a><a href="{root}shortages.html">In shortage now</a>
<a href="{root}track-record.html">Track record</a><a href="{root}about.html">Method &amp;
limits</a><a href="{root}feed.xml">RSS</a></nav></header>
<main>
<p class="disclaimer">{DISCLAIMER}</p>
{body}
</main>
<footer>{fresh}<p>Built from public US government data (openFDA, FDA, data.medicaid.gov) and
Internet Archive copies of FDA's own shortage page. Free and non-commercial.</p></footer>
</body></html>
"""


def sparkline(history: list[dict], width: int = 560, height: int = 90) -> str:
    """SVG of weekly probability; weeks on FDA's shortage list shaded."""
    if not history:
        return ""
    probs = [h["prob"] for h in history]
    top = max([p for p in probs if p is not None] + [BASE_RATE * 4])
    n = len(history)
    step = width / max(n - 1, 1)
    shade = "".join(
        f"<rect x='{i * step - step / 2:.1f}' y='0' width='{step:.1f}' "
        f"height='{height}' class='short'/>"
        for i, h in enumerate(history)
        if h["short"]
    )
    pts = " ".join(
        f"{i * step:.1f},{height - (p / top) * (height - 6) - 3:.1f}"
        for i, p in enumerate(probs)
        if p is not None
    )
    base_y = height - (BASE_RATE / top) * (height - 6) - 3
    first, last = esc(history[0]["week"]), esc(history[-1]["week"])
    return f"""<figure class="spark"><svg viewBox="0 0 {width} {height}" role="img"
aria-label="Weekly estimated risk from {first} to {last}">
{shade}<line x1="0" x2="{width}" y1="{base_y:.1f}" y2="{base_y:.1f}" class="base"/>
<polyline points="{pts}" class="line"/></svg>
<figcaption>Weekly estimated 90-day risk, {first} to {last}. Dashed line: typical drug
({BASE_RATE * 100:.2f}%). Shaded: weeks on FDA's shortage list.</figcaption></figure>"""


def band_badge(band: str | None) -> str:
    if not band:
        return "<span class='badge short'>in shortage</span>"
    return f"<span class='badge {esc(band)}'>{esc(band)}</span>"


def rank_change(e: dict) -> str:
    if e["prev_rank"] is None or e["rank"] is None:
        return "<span class='new'>new</span>"
    d = e["prev_rank"] - e["rank"]
    if d == 0:
        return "–"
    return f"<span class='{'up' if d > 0 else 'down'}'>{'▲' if d > 0 else '▼'}{abs(d)}</span>"


def main_reason(e: dict) -> str:
    return e["reasons"][0] if e["reasons"] else "Drug type only"


def drug_link(item: dict, root: str = "") -> str:
    return f"<a href='{root}drug/{esc(item['slug'])}.html'>{esc(item['name'])}</a>"


def changes_section(changes: dict | None) -> str:
    """This week's real news. Small moves around rank 50 are left out."""
    if changes is None:
        return ""
    groups = [
        ("New FDA-listed shortages", changes["new_shortages"], lambda c: ""),
        ("No longer on FDA's list", changes["resolved_shortages"], lambda c: ""),
        (
            "Moved up a risk band",
            changes["moved_up_band"],
            lambda c: f" ({esc(c['from'])} to {esc(c['to'])})",
        ),
        ("Big jumps in rank", changes["big_jumps"], lambda c: f" (#{c['from']} to #{c['to']})"),
    ]
    blocks = "".join(
        f"<div><h3>{esc(title)}</h3><ul>"
        + "".join(f"<li>{drug_link(c)}{extra(c)}</li>" for c in items[:8])
        + "</ul>"
        + (f"<p class='note'>and {len(items) - 8} more</p>" if len(items) > 8 else "")
        + "</div>"
        for title, items, extra in groups
        if items
    )
    blocks = blocks or "<p>No notable changes this week.</p>"
    return f"<section class='changes'><h2>What changed this week</h2>{blocks}</section>"


def index_page(data: dict) -> str:
    ranked = [e for e in data["families"] if e["rank"] is not None]
    rows = "".join(
        f"<tr><td>{e['rank']}</td><td class='opt'>{rank_change(e)}</td>"
        f"<td><a href='drug/{esc(e['slug'])}.html'>{esc(e['name'])}</a></td>"
        f"<td>{pct(e['prob'])}</td><td class='opt'>{e['multiple']}×</td>"
        f"<td>{band_badge(e['band'])}</td>"
        f"<td class='why opt'>{esc(main_reason(e))}</td></tr>"
        for e in ranked[:WATCHLIST_SIZE]
    )
    legend = "".join(f"<li>{band_badge(b)} {esc(BAND_TEXT[b])}</li>" for _, b in BANDS)
    n_injectable = sum(e["injectable"] for e in ranked[:WATCHLIST_SIZE])
    body = f"""
<h1>Drug shortage watchlist</h1>
<p>The {WATCHLIST_SIZE} drug families with the highest estimated chance that a <strong>new</strong>
FDA-listed shortage starts in the next 90 days, for the week of {esc(data["week"])}.
A typical family's chance is about {BASE_RATE * 100:.2f}%. Families already in shortage are
<a href="shortages.html">listed separately</a>.</p>
<ul class="legend">{legend}</ul>
{changes_section(data.get("changes"))}
<h2>Top {WATCHLIST_SIZE}</h2>
<p class="note">{n_injectable} of these {WATCHLIST_SIZE} are sterile injectables, which shows how
strongly drug type drives risk. To see tablets, capsules and other forms, use
<a href="all.html">All drugs</a>.</p>
<div class="table-wrap"><table>
<thead><tr><th>#</th><th class="opt">Δ wk</th><th>Drug family</th><th>90-day risk</th>
<th class="opt">vs typical</th><th>Band</th><th class="opt">Main reason</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<h2>Look up any drug</h2>
<input id="q" type="search" placeholder="e.g. cisplatin, amoxicillin"
  aria-label="Search drug families">
<ul id="results"></ul>
<script>
const box = document.getElementById('q'), out = document.getElementById('results');
let idx = null;
box.addEventListener('input', async () => {{
  if (!idx) idx = await (await fetch('search.json')).json();
  const q = box.value.trim().toLowerCase();
  out.replaceChildren();
  if (q.length < 2) return;
  for (const e of idx.filter(e => e.n.toLowerCase().includes(q)).slice(0, 20)) {{
    const li = document.createElement('li'), a = document.createElement('a');
    a.href = 'drug/' + e.s + '.html';
    a.textContent = e.n;
    li.append(a, document.createTextNode(' ' + e.b));
    out.append(li);
  }}
}});
</script>"""
    return page("RxRadar: drug shortage watchlist", body, freshness=data["freshness"])


def inspection_section(facts: dict | None) -> str:
    if facts is None:
        return """<p>RxRadar hasn't linked this drug to a factory, so its inspection record is
unknown. That doesn't mean the record is clean.</p>
<p class="note">Links exist only for some generic and injectable drugs, from up to 5 companies'
labels each.</p>"""
    n = facts["factories"]
    return f"""<p>Labels for this drug list {n} {"factory" if n == 1 else "factories"} that RxRadar
matched to FDA records. FDA has drug-quality inspection results for {facts["inspected"]} of them.
In the past 3 years of public results, {facts["failed_3y"]} had an inspection classified Official
Action Indicated (OAI), and {facts["actions_3y"]} had a warning letter, injunction or seizure.</p>
<p class="note">OAI means FDA found significant problems at the time of that inspection. It isn't
a current safety warning: the problems may have been fixed, and they may involve other products
made at the same factory. This list can be incomplete, because labels may name only some sites
and RxRadar reads only some labels. Results can appear here up to about 2 years late, because
FDA doesn't say when most were released. These facts don't affect the risk estimate.</p>"""


def drug_page(e: dict, data: dict, slugs: dict[str, str]) -> str:
    reasons = "".join(f"<li>{esc(r)}</li>" for r in e["reasons"])
    reasons = reasons or (
        "<li>No drug-specific warning signs. Its risk reflects its drug type.</li>"
    )
    peer = f"<p>{esc(e['peer_note'])}</p>" if e.get("peer_note") else ""
    context = f"<p class='note'>{esc(e['context'])}</p>" if e.get("context") else ""
    lost = ""
    if e.get("lost_companies"):
        names = ", ".join(esc(n) for n in e["lost_companies"])
        lost = (
            f"<p>No longer listed compared with about 6 months ago: {names}.</p>"
            "<p class='note'>A listing can end for many reasons, including mergers and "
            "relabeling. It doesn't always mean a company stopped making the drug.</p>"
        )
    alts = e["alternatives"]
    makers = (
        ", ".join(esc(x) for x in alts["labelers"]) or "none listed in the latest NDC directory"
    )
    forms = (
        "".join(
            f"<li><a href='{esc(slugs[o['family']])}.html'>{esc(o['name'])}</a> "
            f"({o['n_labelers']} {'company' if o['n_labelers'] == 1 else 'companies'})</li>"
            if o["family"] in slugs
            else f"<li>{esc(o['name'])} ({o['n_labelers']} companies)</li>"
            for o in alts["other_forms"]
        )
        or "<li>No other forms listed.</li>"
    )
    if e["in_shortage_now"]:
        status = (
            "<p class='status short'>This drug family is on FDA's shortage list now. "
            "RxRadar only estimates the risk of <em>new</em> shortages, so it is not ranked. "
            "See <a href='https://www.accessdata.fda.gov/scripts/drugshortages/'>FDA's drug "
            "shortage database</a> for current status.</p>"
        )
    else:
        status = (
            f"<p class='status'>{band_badge(e['band'])} Estimated chance of a new FDA-listed "
            f"shortage in the next 90 days: <strong>{pct(e['prob'])}</strong> "
            f"({e['multiple']}× a typical drug family). Watchlist rank: {e['rank']} of "
            f"{sum(1 for f in data['families'] if f['rank'])}.</p>"
        )
    body = f"""
<h1>{esc(e["name"])}</h1>
<p class="sub">{esc(e["form"])} · {esc(e.get("group", "Other"))}</p>
{status}
<h2>Why this estimate</h2>
<ul>{reasons}</ul>
{peer}
{context}
<p class="note">Reasons are the model's largest contributions for this week, in plain words.
They describe statistical associations in past data, not causes.</p>
<h2>Risk over time</h2>
{sparkline(e["history"])}
<h2>Who makes it</h2>
<p>{makers}</p>
{lost}
<h2>Factory inspections</h2>
{inspection_section(e.get("inspections"))}
<h2>Other forms of the same ingredient</h2>
<ul>{forms}</ul>
<p class="note">These are supply facts from FDA's NDC directory: other companies and other
forms of the <em>same</em> active ingredient. They are not clinical substitutes. Ask a
pharmacist or prescriber before any change.</p>"""
    return page(f"{e['name']}: RxRadar", body, root="../", freshness=data["freshness"])


def shortages_page(data: dict, slugs: dict[str, str]) -> str:
    short = sorted((e for e in data["families"] if e["in_shortage_now"]), key=lambda e: e["name"])
    items = "".join(
        f"<li><a href='drug/{esc(slugs[e['family']])}.html'>{esc(e['name'])}</a></li>"
        for e in short
    )
    body = f"""
<h1>On FDA's shortage list now</h1>
<p>{len(short)} drug families that RxRadar can map are listed as current shortages by FDA in
the latest capture. They are excluded from the watchlist, which only covers <em>new</em>
shortages. FDA's <a href="https://www.accessdata.fda.gov/scripts/drugshortages/">drug shortage
database</a> is the authoritative source.</p>
<ul class="cols">{items}</ul>"""
    return page("In shortage now: RxRadar", body, freshness=data["freshness"])


def all_page(data: dict) -> str:
    groups = sorted({e["group"] for e in data["families"]} - {"Other"}) + ["Other"]
    group_opts = "".join(f"<option>{esc(g)}</option>" for g in groups)
    form_opts = "".join(f"<option>{esc(f)}</option>" for f in FORM_FILTERS)
    band_opts = "".join(f"<option>{esc(b)}</option>" for _, b in BANDS)
    body = f"""
<h1>All drugs</h1>
<p>Search and filter every drug family RxRadar tracks. Drug groups are for browsing only: being
in the same group doesn't mean two drugs can replace each other.</p>
<form class="filters" onsubmit="return false">
<input id="q" type="search" placeholder="Drug name" aria-label="Search by drug name">
<select id="group" aria-label="Drug group"><option value="">All groups</option>{group_opts}</select>
<select id="form" aria-label="Form"><option value="">All forms</option>{form_opts}</select>
<select id="band" aria-label="Risk band"><option value="">All bands</option>{band_opts}</select>
</form>
<p id="count" class="note" aria-live="polite"></p>
<div class="table-wrap"><table><thead><tr><th>#</th><th>Drug family</th><th>90-day risk</th>
<th>Band</th><th class="opt">Group</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>
const q = document.getElementById('q'), g = document.getElementById('group'),
  f = document.getElementById('form'), b = document.getElementById('band'),
  rows = document.getElementById('rows'), count = document.getElementById('count');
let all = [];
function cell(tr, text, cls) {{
  const td = document.createElement('td');
  td.textContent = text;
  if (cls) td.className = cls;
  tr.append(td);
  return td;
}}
function draw() {{
  const term = q.value.trim().toLowerCase();
  const hits = all.filter(e => (!term || e.n.toLowerCase().includes(term))
    && (!g.value || e.g === g.value) && (!f.value || e.f === f.value)
    && (!b.value || e.d === b.value));
  count.textContent = hits.length + ' drugs' + (hits.length > 200 ? ', showing the first 200' : '');
  rows.replaceChildren();
  for (const e of hits.slice(0, 200)) {{
    const tr = document.createElement('tr');
    cell(tr, e.r ?? '—');
    const link = document.createElement('a');
    link.href = 'drug/' + e.s + '.html';
    link.textContent = e.n;
    cell(tr, '').append(link);
    cell(tr, e.b);
    cell(tr, e.d ?? 'in shortage');
    cell(tr, e.g, 'opt');
    rows.append(tr);
  }}
}}
fetch('search.json').then(r => r.json()).then(d => {{ all = d; draw(); }});
[q, g, f, b].forEach(el => el.addEventListener('input', draw));
</script>"""
    return page("All drugs: RxRadar", body, freshness=data["freshness"])


def is_fragile(e: dict) -> bool:
    """Few listed makers of a generic. Brand-only drugs are single-source by
    design, so they'd drown out the cases worth attention."""
    return e["n_labelers"] in (1, 2) and bool(e.get("n_generic_labelers"))


def lost_a_company(e: dict) -> bool:
    """A lost listing only matters when few remain. Elsewhere it's mostly
    repackagers coming and going: about 770 families would qualify otherwise."""
    if not e.get("lost_companies") or not e["n_labelers"]:
        return False
    return e["n_labelers"] <= LOST_COMPANY_MAX_LEFT and bool(
        e.get("n_generic_labelers") or e["injectable"]
    )


def fragile_page(data: dict) -> str:
    fams = data["families"]
    lost = sorted(filter(lost_a_company, fams), key=lambda e: (e["n_labelers"] or 0, e["name"]))
    few = sorted(
        filter(is_fragile, fams), key=lambda e: (e["n_labelers"], not e["injectable"], e["name"])
    )

    def row(e: dict) -> str:
        status = "in shortage" if e["in_shortage_now"] else esc(e["band"])
        return (
            f"<tr><td>{drug_link(e)}</td><td>{e['n_labelers']}</td>"
            f"<td class='opt'>{esc(e['group'])}</td><td>{status}</td></tr>"
        )

    head = (
        "<thead><tr><th>Drug family</th><th>Companies listed</th><th class='opt'>Group</th>"
        "<th>Risk band</th></tr></thead>"
    )
    lost_rows = "".join(row(e) for e in lost[:FRAGILE_LIMIT])
    few_rows = "".join(row(e) for e in few[:FRAGILE_LIMIT])
    body = f"""
<h1>Fragile supply</h1>
<p>Plain facts from FDA's NDC directory, not predictions. A drug with few companies listed has
less backup if one has a problem. That doesn't mean a shortage is coming, and a listed company
isn't always the one making the drug.</p>
<h2>Lost a company in the past 6 months, {LOST_COMPANY_MAX_LEFT} or fewer left ({len(lost)})</h2>
<div class="table-wrap"><table>{head}<tbody>{lost_rows}</tbody></table></div>
<h2>Generics with one or two companies listed ({len(few)})</h2>
<div class="table-wrap"><table>{head}<tbody>{few_rows}</tbody></table></div>
<p class="note">Lists show up to {FRAGILE_LIMIT} drugs. Company names are compared loosely, so a
merger or rename can appear as a lost company, and many listed companies are repackagers rather
than manufacturers.</p>"""
    return page("Fragile supply: RxRadar", body, freshness=data["freshness"])


def rate(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def track_page(track: dict | None, data: dict) -> str:
    """Live results only; never mixed with the backtest (decisions D9)."""
    if not track or not track["weeks_frozen"]:
        body = """<h1>Track record</h1>
<p>Tracking hasn't started yet. Once it does, each week's predictions are saved before
their outcomes are known, and scored here in public.</p>"""
        return page("Track record: RxRadar", body, freshness=data["freshness"])
    s = track["summary"]
    rows = "".join(
        f"<tr><td>Top {k}</td><td>{rate(s[f'recall@{k}'])}</td>"
        f"<td>{rate(s[f'rule_recall@{k}'])}</td><td>{rate(s[f'random_recall@{k}'])}</td></tr>"
        for k in KS
    )
    prec = "".join(
        f"<tr><td>Top {k}</td><td>{rate(s[f'precision@{k}'])}</td>"
        f"<td>{rate(s[f'rule_precision@{k}'])}</td><td>{rate(s['random_precision'])}</td></tr>"
        for k in KS
    )
    onset_rows = "".join(
        f"<tr><td>{esc(o['start'])}</td><td>{esc(o['name'])}</td>"
        f"<td>{'—' if o['rank'] is None else o['rank']}</td><td>{o['lead_days']} days</td>"
        f"<td>{'caught (top 100)' if o['hit@100'] else 'missed'}</td></tr>"
        for o in reversed(track["onsets"])
    )
    onsets = (
        "<div class='table-wrap'><table><thead><tr><th>Shortage began</th><th>Drug family</th>"
        "<th>Rank before it</th><th>Lead</th><th>Result</th></tr></thead>"
        f"<tbody>{onset_rows}</tbody></table></div>"
        if onset_rows
        else "<p>No shortage has been scored yet.</p>"
    )
    bands = ""
    if track["bands"]:
        band_rows = "".join(
            f"<tr><td>{band_badge(b['band'])}</td><td>{rate(b['predicted'])}</td>"
            f"<td>{rate(b['observed'])}</td><td>{b['family_weeks']:,}</td></tr>"
            for b in track["bands"]
        )
        bands = (
            "<h2>Predicted vs actual, by risk band</h2><div class='table-wrap'><table><thead>"
            "<tr><th>Band</th><th>Predicted</th><th>Actual</th><th>Family-weeks</th></tr></thead>"
            f"<tbody>{band_rows}</tbody></table></div>"
        )
    early = ""
    n_scored = track["onsets_scored"]
    if n_scored < MIN_ONSETS_FOR_BANDS:
        so_far = (
            f"Only {n_scored} shortages have been scored."
            if n_scored
            else (
                "No shortages have been scored yet: the first week finishes its 90 days about "
                "three months after tracking began."
            )
        )
        early = (
            f"<p class='status'><strong>Too early to judge.</strong> {so_far} New FDA-listed "
            "shortages are rare, so these numbers will swing a lot until there are dozens.</p>"
        )
    body = f"""
<h1>Track record</h1>
<p>Each week's predictions are saved before their outcomes are known, and never changed
afterwards. The saved files are public, with their timestamps, on the
<a href="{REPO_URL}/tree/predictions">predictions branch</a>. These are live results, kept
separate from the backtest on the <a href="about.html">method page</a>. The
<a href="{REPO_URL}/blob/main/docs/decisions.md">scoring rules (D9)</a> were published before
any result existed.</p>
{early}
<ul class="stats">
<li><strong>{esc(track["started"])}</strong> tracking began</li>
<li><strong>{track["weeks_frozen"]}</strong> weeks saved</li>
<li><strong>{track["weeks_pending"]}</strong> still inside their 90 days</li>
<li><strong>{track["weeks_scored"]}</strong> weeks scored</li>
<li><strong>{track["onsets_scored"]}</strong> shortages scored</li>
</ul>
<h2>Shortages flagged at least 4 weeks ahead</h2>
<div class="table-wrap"><table><thead><tr><th>Watchlist size</th><th>RxRadar</th>
<th>Simple rule</th><th>Random</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Flagged drugs that went short within 90 days</h2>
<div class="table-wrap"><table><thead><tr><th>Watchlist size</th><th>RxRadar</th>
<th>Simple rule</th><th>Random</th></tr></thead><tbody>{prec}</tbody></table></div>
{bands}
<h2>Every scored shortage</h2>
{onsets}
<p class="note">The simple rule ranks drugs by recent shortages, injectable form, few makers and
shortages in other forms. {track["weeks_unknown"]} weeks are unscorable because of data gaps,
and {track["onsets_too_soon"]} shortages began too soon after tracking started to score.</p>"""
    return page("Track record: RxRadar", body, freshness=data["freshness"])


def about_page(data: dict) -> str:
    body = f"""
<h1>Method and limits</h1>
<h2>What the number means</h2>
<p>For each drug family (same active ingredient, dosage form and route), RxRadar estimates
the chance that FDA lists a <strong>new</strong> shortage within 90 days. A typical family's
chance is about {BASE_RATE * 100:.2f}%. Bands are multiples of that: high is 8× or more,
elevated 4–8×, moderate 2–4×.</p>
<h2>How well it worked in testing</h2>
<p>In a walk-forward backtest (2017 and 2020–2026, retrained each January using only data
public at the time):</p>
<ul>
<li>A watchlist of the top 100 families included about <strong>1 in 4</strong> new shortages
at least 4 weeks before FDA listed them. A random list caught about 1 in 30.</li>
<li>About <strong>1 in 50</strong> flagged family-weeks was followed by a new shortage
within 90 days. Most drugs on the list do not go short.</li>
<li>Most of the signal comes from what kind of drug it is (sterile injectables and old
generics). Manufacturer counts, recalls and prices add a little, but the test cannot
show that the gain is real rather than chance.</li>
</ul>
<h2>Data (all public)</h2>
<ul>
<li>FDA's drug shortage list: daily captures since 2026-10-30, and Internet Archive copies
since 2014 for history.</li>
<li>FDA NDC directory: manufacturers and products, including archived copies since 2016.</li>
<li>openFDA drug recall (enforcement) reports and Drugs@FDA.</li>
<li>Medicaid NADAC: weekly retail pharmacy acquisition prices.</li>
</ul>
<h2>Limits</h2>
<ul>
<li>Only shortages FDA lists are counted. Other shortage lists are not used.</li>
<li>About 3% of FDA's shortage names (mostly multi-ingredient products such as IV
nutrition and dialysis fluids) can't be matched to a drug family and are missing.</li>
<li>FDA inspection results are not used yet.</li>
<li>Prices come from retail pharmacies, so hospital injectables often have no price.</li>
<li>Model: calibrated logistic regression, trained through
{esc(data["model"]["trained_through"])}.</li>
</ul>
<p><strong>Not clinical advice.</strong> RxRadar describes supply risk only. It does not
recommend treatments or substitutes.</p>"""
    return page("Method and limits: RxRadar", body, freshness=data["freshness"])


def feed(data: dict, base_url: str) -> str:
    """RSS items for real changes: risk-band rises and big rank jumps."""
    by_slug = {e["slug"]: e for e in data["families"]}
    changes = data.get("changes") or {"moved_up_band": [], "big_jumps": []}
    updates = [(c, f"moved from {c['from']} to {c['to']} risk") for c in changes["moved_up_band"]]
    updates += [(c, f"jumped from #{c['from']} to #{c['to']}") for c in changes["big_jumps"]]
    items = []
    for c, why in updates:
        e = by_slug[c["slug"]]
        link = f"{base_url}drug/{e['slug']}.html"
        guid = f"{e['slug']}-{data['week']}"
        reasons = "; ".join(e["reasons"]) or "no drug-specific warning signs"
        desc = (
            f"Estimated 90-day risk {pct(e['prob'])}. {reasons}. "
            "Supply risk only, not clinical advice."
        )
        items.append(
            f"<item><title>{xml_escape(e['name'])}: {xml_escape(why)}</title>"
            f"<link>{xml_escape(link)}</link>"
            f"<guid isPermaLink='false'>{xml_escape(guid)}</guid>"
            f"<description>{xml_escape(desc)}</description></item>"
        )
    now = dt.datetime.now(dt.UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
        f"<title>RxRadar watchlist changes</title><link>{xml_escape(base_url)}</link>"
        "<description>Drug families moving up a risk band or jumping in rank. "
        "Supply risk only, not clinical advice.</description>"
        f"<lastBuildDate>{now}</lastBuildDate>{''.join(items)}</channel></rss>"
    )


def search_label(e: dict) -> str:
    return "in shortage now" if e["in_shortage_now"] else pct(e["prob"])


def form_filter(form: str) -> str:
    return next((f for f in FORM_FILTERS if f.lower() in form.lower()), "Other")


def build_site(scores_path: Path | None = None, out: Path | None = None) -> Path:
    from rxradar.model.score import site_data_dir

    scores_path = scores_path or site_data_dir() / "scores.json"
    out = out or Path("public")
    base_url = os.environ.get("RXRADAR_BASE_URL", "./")
    data = json.loads(scores_path.read_text())
    if out.exists():
        shutil.rmtree(out)
    (out / "drug").mkdir(parents=True)
    shutil.copy(Path(__file__).parent / "static" / "style.css", out / "style.css")
    slugs = {e["family"]: e["slug"] for e in data["families"]}
    (out / "index.html").write_text(index_page(data))
    (out / "shortages.html").write_text(shortages_page(data, slugs))
    (out / "about.html").write_text(about_page(data))
    (out / "all.html").write_text(all_page(data))
    (out / "fragile.html").write_text(fragile_page(data))
    track_path = scores_path.parent / "track_record.json"
    track = json.loads(track_path.read_text()) if track_path.exists() else None
    (out / "track-record.html").write_text(track_page(track, data))
    for e in data["families"]:
        (out / "drug" / f"{e['slug']}.html").write_text(drug_page(e, data, slugs))
    search = [
        {
            "n": e["name"],
            "s": e["slug"],
            "b": search_label(e),
            "r": e["rank"],
            "d": e["band"],
            "g": e.get("group", "Other"),
            "f": form_filter(e["form"]),
        }
        for e in data["families"]
    ]
    (out / "search.json").write_text(json.dumps(search, separators=(",", ":")))
    (out / "feed.xml").write_text(feed(data, base_url))
    (out / ".nojekyll").write_text("")
    return out
