"""Plant pages: the failure simulator, one page per plant, and the Helene replay.

Everything here describes exposure. Labels show which plants may make a drug, not how
much each makes, so these pages never say a drug will go short or give a share of
supply. A drug with no listed plant is unknown, not safe. Past data doesn't show that
few known plants means more shortages (decisions D13), and the simulator says so.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from rxradar.site.build import band_badge, esc, page, show_more, stat_strip

MARION_FEI_SLUG_START = "baxter-healthcare-corporation-marion-"
HELENE_LISTED = (
    (
        "Dextrose 70%",
        "5SL0G7R0OK|",
        "FDA posted this on 2024-10-11. DrugSupply Watch's dextrose family covers every "
        "strength, and other strengths were already listed.",
    ),
    (
        "Lactated Ringer's",
        "451W47IQ8X+660YQ98I10+M4I0D6VV5M+TU7HW0W0QT|",
        "Still on FDA's list in the first archive copy after the gap, on 2025-09-05.",
    ),
    (
        "Peritoneal dialysis solution",
        (
            "02F3473H9O+2NX48Z0A9G+451W47IQ8X+M4I0D6VV5M+TU7HW0W0QT|",
            "02F3473H9O+451W47IQ8X+5SL0G7R0OK+M4I0D6VV5M+TU7HW0W0QT|",
        ),
        "Two dialysis solution families carry Marion on their labels.",
    ),
)
HELENE_SOURCES = (
    (
        "American Medical Association",
        "https://www.ama-assn.org/delivering-care/public-health/"
        "iv-shortage-update-baxter-facility-damage-after-hurricane-north",
    ),
    (
        "WSOC-TV",
        "https://www.wsoctv.com/news/local/"
        "fda-declares-iv-shortage-after-western-nc-pharmaceutical-plant-flooded-due-helene/"
        "PDKGUZ7CRFD2ZB2R5JZOMN5RAA",
    ),
)
EXPOSURE_NOTE = """Exposed means a drug's labels name this plant. It doesn't mean the drug
would go short: labels don't show how much each plant makes, a listed plant may not be
in use, and other companies may step in."""
D13_NOTE = """In past data, drugs with 1 or 2 known plants went short about 1.3× as often
as drugs with 3 or more, but the 95% range of 0.9× to 2.0× includes no difference. So a
plant count here is a fact about the supply, not a risk score."""
PLANTS_SHOWN = 6


def place(p: dict) -> str:
    return ", ".join(x for x in (p["city"], p["region"], p["country"]) if x) or "Location unknown"


def plants_by_family(plant_map: dict | None) -> dict[str, list[dict]] | None:
    """Family -> [(plant, role)], or None when there's no map to speak of."""
    if plant_map is None:
        return None
    index = defaultdict(list)
    for p in plant_map["plants"]:
        for family in p["finished"]:
            index[family].append((p, "finished"))
        for family in p["ingredient"]:
            index[family].append((p, "ingredient"))
    return index


def known_plants_section(rows: list | None, root: str = "../") -> str:
    """The drug page's list of plants. ``rows`` None means there's no map at all."""
    if rows is None:
        return ""
    if not rows:
        return """<h2>Known plants</h2>
<p>This drug's labels don't name a plant that makes it, so its plants are unknown. That
doesn't mean it's safe from a plant failure.</p>"""

    def line(item: tuple[dict, str]) -> str:
        p, _ = item
        where = esc(p["country"] or "location unknown")
        return f"<a href='{root}plant/{esc(p['slug'])}.html'>{esc(p['name'])}</a> ({where})"

    finished = sorted((r for r in rows if r[1] == "finished"), key=lambda r: r[0]["name"])
    ingredient = sorted((r for r in rows if r[1] == "ingredient"), key=lambda r: r[0]["name"])
    parts = ["<h2>Known plants</h2>"]
    if finished:
        n = len(finished)
        parts.append(f"<p>{n} {'plant is' if n == 1 else 'plants are'} named as making it.</p>")
        parts.append(show_more([line(r) for r in finished], PLANTS_SHOWN, "plants", "makers"))
    if ingredient:
        parts.append("<h3>Active ingredient made at</h3>")
        parts.append(show_more([line(r) for r in ingredient], PLANTS_SHOWN, "plants", "makers"))
    parts.append(
        "<p class='note'>From the drug's labels. A listed plant may not be in use, and "
        "labels don't show how much each plant makes.</p>"
    )
    return "".join(parts)


SIMULATOR_SCRIPT = """<script>
const q = document.getElementById('sim-q'), hits = document.getElementById('sim-hits'),
  out = document.getElementById('sim-out'), title = document.getElementById('sim-title'),
  stats = document.getElementById('sim-stats'), rows = document.getElementById('sim-rows'),
  more = document.getElementById('sim-more');
let map = null;
const load = async () => map ??= await (await fetch('plants.json')).json();
q.addEventListener('focus', load, {once: true});
const P = {slug: 0, name: 1, city: 2, region: 3, country: 4, fin: 5, ing: 6};

function choices(term) {
  const t = term.toLowerCase(), list = [];
  const add = (hash, label, n) => list.push({hash, label, n});
  const count = f => map.plants.filter(f).length;
  const seen = new Set();
  for (const p of map.plants) {
    if (p[P.country] && p[P.country].toLowerCase().includes(t) && !seen.has('c' + p[P.country])) {
      seen.add('c' + p[P.country]);
      add('country=' + p[P.country], 'Every plant in ' + p[P.country],
        count(x => x[P.country] === p[P.country]));
    }
    if (p[P.country] === 'United States' && p[P.region].toLowerCase().includes(t)
        && !seen.has('s' + p[P.region])) {
      seen.add('s' + p[P.region]);
      add('state=' + p[P.region], 'Every plant in ' + p[P.region],
        count(x => x[P.region] === p[P.region]));
    }
  }
  const named = map.plants.filter(p => p[P.name].toLowerCase().includes(t));
  if (named.length > 1) add('company=' + term, 'Every plant named "' + term + '"', named.length);
  for (const p of named.concat(map.plants.filter(p => p[P.city].toLowerCase().includes(t)
      && !named.includes(p))).slice(0, 12)) {
    const where = [p[P.city], p[P.country]].filter(Boolean).join(', ');
    add('plant=' + p[P.slug], p[P.name] + ', ' + where, 0);
  }
  return list.slice(0, 16);
}

function selected(hash) {
  const [kind, ...rest] = hash.split('='), value = rest.join('=');
  const f = {
    plant: p => p[P.slug] === value,
    country: p => p[P.country] === value,
    state: p => p[P.country] === 'United States' && p[P.region] === value,
    company: p => p[P.name].toLowerCase().includes(value.toLowerCase()),
  }[kind];
  if (!f) return null;
  const plants = map.plants.filter(f);
  if (!plants.length) return null;
  const names = {country: 'Every plant in ' + value, state: 'Every plant in ' + value,
    company: 'Every plant named "' + value + '"',
    plant: [plants[0][P.name], plants[0][P.city], plants[0][P.country]].filter(Boolean).join(', ')};
  return {plants, name: names[kind]};
}

function stat(number, label) {
  const li = document.createElement('li'), strong = document.createElement('strong');
  strong.textContent = number.toLocaleString('en-US');
  li.append(strong, ' ' + label);
  return li;
}

function show() {
  const hash = decodeURIComponent(location.hash.slice(1));
  const pick = hash ? selected(hash) : null;
  out.hidden = !pick;
  if (!pick) return;
  const lost = new Map(), ingredient = new Set();
  for (const p of pick.plants) {
    for (const d of p[P.fin]) lost.set(d, (lost.get(d) || 0) + 1);
    for (const d of p[P.ing]) ingredient.add(d);
  }
  const exposed = [...lost].map(([d, n]) => ({d, left: map.known[d] - n}))
    .sort((a, b) => a.left - b.left || map.drugs[a.d][0].localeCompare(map.drugs[b.d][0]));
  const none = exposed.filter(e => e.left === 0).length;
  title.textContent = pick.name;
  stats.replaceChildren(
    stat(pick.plants.length, pick.plants.length === 1 ? 'plant' : 'plants'),
    stat(exposed.length, 'drug families named as made here'),
    stat(none, 'with no other known plant'),
    stat(exposed.filter(e => e.left === 1).length, 'with 1 other known plant'));
  rows.replaceChildren();
  for (const e of exposed.slice(0, 300)) {
    const tr = document.createElement('tr'), a = document.createElement('a'),
      name = document.createElement('td'), left = document.createElement('td');
    a.href = 'drug/' + map.drugs[e.d][1] + '.html';
    a.textContent = map.drugs[e.d][0];
    name.append(a);
    left.className = 'num';
    left.textContent = e.left;
    tr.append(name, left);
    rows.append(tr);
  }
  const extra = [...ingredient].filter(d => !lost.has(d)).length;
  more.textContent = (exposed.length > 300 ? 'Showing the first 300. ' : '')
    + (extra ? extra + ' more drug families use an active ingredient made here. ' : '');
}

q.addEventListener('input', async () => {
  await load();
  hits.replaceChildren();
  const term = q.value.trim();
  if (term.length < 2) return;
  const found = choices(term);
  if (!found.length) {
    const li = document.createElement('li');
    li.textContent = 'No plant, company, state or country matches that.';
    hits.append(li);
  }
  for (const c of found) {
    const li = document.createElement('li'), a = document.createElement('a');
    a.href = '#' + encodeURIComponent(c.hash);
    a.textContent = c.label + (c.n ? ' (' + c.n + ')' : '');
    li.append(a);
    hits.append(li);
  }
});
addEventListener('hashchange', async () => { await load(); show(); });
if (location.hash) load().then(show);
</script>"""


def simulator_page(plant_map: dict, data: dict) -> str:
    marion = next(
        (p["slug"] for p in plant_map["plants"] if p["slug"].startswith(MARION_FEI_SLUG_START)), ""
    )
    n_plants = len(plant_map["plants"])
    families = {f for p in plant_map["plants"] for f in p["finished"]}
    body = f"""
<h1>Plant failure simulator</h1>
<p class="lede">Pick a plant, company, US state or country to see which drugs are named as
made there, and how many other known plants each one has. This shows exposure, not a
prediction.</p>
<div class="lookup search-row">
<label for="sim-q">Search</label>
<input id="sim-q" type="search" placeholder="e.g. Baxter, Puerto Rico, India" autocomplete="off">
<ul id="sim-hits" class="hits"></ul>
</div>
<p>Try <a href="#plant={esc(marion)}">Baxter's plant in Marion, North Carolina</a>,
<a href="#state=Puerto%20Rico">Puerto Rico</a>, <a href="#country=India">India</a> or
<a href="#country=China">China</a>. Baxter's plant is the one Hurricane Helene flooded:
<a href="helene.html">see how that compares</a>.</p>
<section id="sim-out" aria-live="polite" hidden>
<h2 id="sim-title"></h2>
<ul id="sim-stats" class="stats"></ul>
<div class="table-wrap"><table><thead><tr><th>Drug family</th>
<th class="num">Other known plants</th></tr></thead><tbody id="sim-rows"></tbody></table></div>
<p id="sim-more" class="note"></p>
</section>
<p class="note">{EXPOSURE_NOTE}</p>
<p class="note">{D13_NOTE}</p>
<details><summary>What this map covers</summary>
<p class="note">{n_plants:,} plants, linked to {len(families):,} of
{len(data["families"]):,} drug families, from DailyMed labels dated
{esc(plant_map["as_of"])}. Other families have no plant named for their current products,
so they're unknown and can't appear here. Only making the drug or its active ingredient
counts: testing, labeling and repacking don't. Foreign-made ingredients are often missing
from labels, so this understates dependence on other countries.</p></details>
{SIMULATOR_SCRIPT}"""
    return page("Plant failure simulator: DrugSupply Watch", body, freshness=data["freshness"],
                active="plants.html")  # fmt: skip


def plant_page(p: dict, fams: dict, known: dict[str, int], data: dict) -> str:
    def row(family: str, role: str) -> str:
        e = fams[family]
        others = known[family] - 1 if role == "finished" else None
        left = "—" if others is None else others
        return (
            f"<tr><td><a href='../drug/{esc(e['slug'])}.html'>{esc(e['name'])}</a></td>"
            f"<td>{'Drug' if role == 'finished' else 'Active ingredient'}</td>"
            f"<td class='num'>{left}</td><td>{band_badge(e['band'])}</td></tr>"
        )

    finished = sorted(p["finished"], key=lambda f: (known[f], fams[f]["name"]))
    ingredient = sorted(p["ingredient"], key=lambda f: fams[f]["name"])
    rows = "".join(row(f, "finished") for f in finished)
    rows += "".join(row(f, "ingredient") for f in ingredient)
    sole = sum(known[f] == 1 for f in finished)
    stats = stat_strip(
        [
            (f"{len(finished):,}", "drug families named as made here"),
            (f"{sole:,}", "with no other known plant"),
            (f"{len(ingredient):,}", "using an active ingredient made here"),
        ]
    )
    if p["inspected"]:
        record = (
            f"<p>In the past 3 years of public results, this plant had {p['failed_3y']} "
            f"inspection{'' if p['failed_3y'] == 1 else 's'} classified Official Action "
            f"Indicated (OAI) and {p['actions_3y']} warning "
            f"letter{'' if p['actions_3y'] == 1 else 's'}, injunction"
            f"{'' if p['actions_3y'] == 1 else 's'} or seizure"
            f"{'' if p['actions_3y'] == 1 else 's'}.</p>"
            "<p class='note'>OAI means FDA found significant problems at the time of that "
            "inspection. It isn't a current safety warning, and the problems may have been "
            "fixed.</p>"
        )
    else:
        record = "<p>No FDA inspection is linked to this plant, so its record is unknown.</p>"
    body = f"""
<p class="crumbs"><a href="../plants.html">Plant simulator</a> /
{esc(p["country"] or "Unknown")}</p>
<h1>{esc(p["name"])}</h1>
<p class="sub">{esc(place(p))}</p>
{stats}
<p class="note">{EXPOSURE_NOTE}</p>
<h2>FDA inspections</h2>
{record}
<h2>Drugs named as made here</h2>
<div class="table-wrap"><table><thead><tr><th>Drug family</th><th>Made here</th>
<th class="num">Other known plants</th><th>Risk band</th></tr></thead>
<tbody>{rows}</tbody></table></div>"""
    return page(f"{p['name']}: DrugSupply Watch", body, root="../", freshness=data["freshness"],
                active="plants.html")  # fmt: skip


def helene_page(plant_map: dict, fams: dict, data: dict) -> str:
    linked = plant_map["helene"]

    def matches(prefixes: str | tuple) -> list[str]:
        wanted = (prefixes,) if isinstance(prefixes, str) else prefixes
        return [f for f in linked if f.startswith(wanted)]

    rows = "".join(
        f"<tr><td>{esc(label)}</td><td>{'Yes' if matches(key) else 'No'}</td>"
        f"<td>{esc(note)}</td></tr>"
        for label, key, note in HELENE_LISTED
    )
    items = "".join(
        f"<li><a href='drug/{esc(fams[f]['slug'])}.html'>{esc(fams[f]['name'])}</a></li>"
        for f in sorted((f for f in linked if f in fams), key=lambda f: fams[f]["name"])
    )
    sources = " · ".join(f"<a href='{esc(url)}'>{esc(name)}</a>" for name, url in HELENE_SOURCES)
    body = f"""
<div class="prose">
<p class="crumbs"><a href="plants.html">Plant simulator</a> / Hurricane Helene</p>
<h1>Replay: Hurricane Helene</h1>
<p class="lede">In late September 2024, Helene flooded Baxter's plant in Marion, North
Carolina. Would the labels public before the storm have pointed to the drugs that went
on FDA's shortage list afterwards?</p>
<p>For the 3 product groups FDA added, yes. All of them were on labels naming this plant
before the storm.</p>
</div>
<div class="table-wrap"><table><thead><tr><th>Added to FDA's list</th>
<th>Named this plant before the storm</th><th>Note</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<div class="prose">
<h2>Why this is an example, not proof</h2>
<ul>
<li>It's 1 event, chosen because it's famous.</li>
<li>The list of what FDA added comes from news reports, checked by hand, because
DrugSupply Watch's archive of FDA's list has a gap from 2024-09-06 to 2025-09-05.</li>
<li>Labels named this plant for {len(linked)} drug families in all, so the 3 groups above
aren't the whole story. The other families can't be scored because of the gap, and many
may have been covered by imports or other plants.</li>
<li>Label history reads up to 5 labels per family and includes sites that only test or
pack, so this list isn't the same as the simulator's.</li>
</ul>
<h2>Every family naming this plant before the storm ({len(linked)})</h2>
<ul class="cols">{items}</ul>
<p class="note">Sources for FDA's additions: {sources}. Dates from openFDA.</p>
</div>"""
    return page("Replay: Hurricane Helene: DrugSupply Watch", body, freshness=data["freshness"],
                active="plants.html")  # fmt: skip


def build_plant_pages(plant_map: dict | None, data: dict, out: Path) -> None:
    if plant_map is None:
        body = """<h1>Plant failure simulator</h1>
<p class="lede">The plant map hasn't been built yet.</p>"""
        (out / "plants.html").write_text(
            page("Plant failure simulator: DrugSupply Watch", body, active="plants.html")
        )
        return
    fams = {e["family"]: e for e in data["families"]}
    known: dict[str, int] = defaultdict(int)
    for p in plant_map["plants"]:
        for family in p["finished"]:
            known[family] += 1
    (out / "plants.html").write_text(simulator_page(plant_map, data))
    (out / "helene.html").write_text(helene_page(plant_map, fams, data))
    (out / "plant").mkdir()
    for p in plant_map["plants"]:
        (out / "plant" / f"{p['slug']}.html").write_text(plant_page(p, fams, known, data))
    index = {f: i for i, (f, _) in enumerate(fams.items())}
    compact = {
        "drugs": [[e["name"], e["slug"]] for e in fams.values()],
        "known": [known[f] for f in fams],
        "plants": [
            [p["slug"], p["name"], p["city"], p["region"], p["country"],
             [index[f] for f in p["finished"]], [index[f] for f in p["ingredient"]]]
            for p in plant_map["plants"]
        ],
    }  # fmt: skip
    (out / "plants.json").write_text(json.dumps(compact, separators=(",", ":")))
