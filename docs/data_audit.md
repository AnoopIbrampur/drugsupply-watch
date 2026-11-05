# Phase 1 data audit

*Snapshot date: 2026-10-29. Every number here was produced by code in this repo
(`scripts/audit_*.py`, `src/rxradar/ingest/*`) against raw snapshots in
`data/raw/<source>/2026-10-29/`. Re-run with `make ingest`, then the scripts.*

## Source status at a glance

| Source | Access | History | Key identifiers | Status |
|---|---|---|---|---|
| openFDA drug shortages | Bulk zip (0.4 MB), no key needed | **Current state only** (1,604 records) | package NDC, generic name, openfda UNII/route/spl_set_id | ✅ ingested |
| FDA shortage list page, archived by Wayback Machine | CDX API + `id_` raw captures | 2014-06-22 → 2026-10-24, about daily, 462 weekly captures used | Listing name (= openFDA `generic_name`), status | ✅ ingested (label source, see D2) |
| openFDA NDC directory | Bulk zip (26 MB) | Current listings only | product/package NDC, labeler, UNII | ✅ ingested |
| FDA NDC text files incl. `ndc_excluded.zip` | Bulk zips on accessdata.fda.gov | **207k delisted products with start/end marketing dates** (no ingredient/form/route; see §5e.7) | product NDC, labeler name, application number | ✅ ingested |
| openFDA Drugs@FDA | Bulk zip | Approved applications incl. discontinued products | application number, ingredients, form, route | ✅ ingested (Phase 2) |
| openFDA substance (GSRS) | Bulk zip (2.4 GB JSON) | Reference | UNII, names/synonyms, active-moiety links | ✅ ingested (Phase 2) |
| FDA establishment registrations (DECRS, `drls_reg`/`drls_excluded`) | Bulk zips | Current plus excluded registrations | **FEI, DUNS**, firm name, address, operations | ✅ ingested |
| openFDA drug enforcement (recalls) | Bulk zip (3.8 MB) | report_date 2012-06 → 2026-10 (18,048 recalls) | recalling firm, free-text product, 17% have NDC block | ✅ ingested |
| FDA warning letters (fda.gov table export) | xlsx export | **Capped at 1,000 rows** (letters posted ~2021–2026) | company name only, no FEI | ⚠️ partial |
| FDA Data Dashboard inspections / compliance actions | Qlik JS export or API with credentials | unknown (not verified; needs access) | FEI, legal name, classification NAI/VAI/OAI (per dashboard page) | ❌ blocked: needs owner action (§5e) |
| Medicaid NADAC | data.medicaid.gov yearly CSVs + DKAN query API | 2013-11-28 → 2026-10-28, weekly | 11-digit NDC | ✅ fetcher works; full download slow (§5e) |
| DailyMed SPL (NLM) | REST API per set id | Current label version | establishment DUNS + operations | 🔍 probed only (§5c) |

---

## 5a. Do we have labels? (Resolved/historical shortages)

**openFDA alone: no.** The shortage endpoint holds 1,604 package-level records:
1,142 *Current*, 443 *To Be Discontinued*, and only **19 *Resolved***. At
ingredient level that is 233 generic names (69 current, 170 discontinued, 5
resolved). Resolved shortages drop off after a short time. `initial_posting_date`
goes back to 2012, but only for shortages that are *still* listed, which makes it a
survivor sample: no records start in 2013–2014, and 2024 has only 11.

FDA's own shortage page shows the same set. It links a
`shortages_current_and_historical.xlsx`, which **returns HTTP 404** (surprise,
logged here).

**Workaround that works: Wayback Machine captures of FDA's list page.**
- The CDX index has 1,435 daily captures (HTTP 200) from 2014-06-22 to
  2026-10-24. We take the first capture of each ISO week, 462 in total; 461
  downloaded and parsed (118–575 listing rows each, no parse failures).
- Every listing has a name and a status code (c/r/d) in its detail-page link.
  The 2014 and 2026 page layouts both parse with the same regex.
- **Validation:** openFDA lists 5 Current shortages with 2026 posting dates.
  - **4 of 5** (furosemide oral solution, isocarboxazid tablet, ifosfamide
    injection, pentostatin injection) appear in the archive as new episodes
    3–6 days after FDA's `initial_posting_date`. That lag equals our weekly
    capture spacing, which suggests archive start dates are trustworthy and
    point-in-time safe.
  - The 5th (dextrose 5% injection, posted 2026-07-20 for Baxter) is a new
    package added to a dextrose shortage listed since 2022. So it is not a new
    family episode. Note that `initial_posting_date` is per package, not per
    shortage.

Episodes (prototype in `transform/shortage_episodes.py`):
- The key is an approximate ingredient + form group.
- Observed absences under 8 weeks are merged.
- Absence is measured only across captures that exist. A drug that is Current
  on both sides of a capture gap counts as one continuous episode.

| | |
|---|---|
| Distinct listing names, 2014–2026 | 2,090 (1,427 approximate family keys) |
| Episodes | 537 |
| …with an observed start (not censored) | **350**, across 317 families |
| …left-censored at first capture (2014-06) | 74 |
| …started during a capture gap > 21 days (start known only to within the gap) | 113 (2014: 30, 2019: 36, 2018: 19, 2015: 12, 2025: 6, other years ≤ 5) |
| Duration (first to last capture as Current) | median 504 days, p90 ~1,770 days |
| Starts that recur in a family with an earlier episode | 19% |
| Status codes seen | only c / r / d in every year (no unparsed codes) |

Uncensored new starts per year (8-week merge; 4- and 26-week merges change
these by at most 3):

| 2014* | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 18 | 20 | 38 | 35 | 11 | 46 | 40 | 46 | 78† | 8 | 0‡ | 5 |

\* from June. \*\* to October. † includes ~42 artifacts from FDA's renaming
(below). ‡ the archive has a 364-day gap from 2024-09-06 to 2025-09-05, so no
2025 start can be dated.

**Surprises logged:**
1. **FDA renamed its listings on 2023-09-18**, switching to SPL-style names
   ("Amifostine Injection" became "Amifostine Injection, Powder, Lyophilized, For
   Solution"). In one week, 134 names appeared and 99 disappeared. Keyed by raw
   name this produced 129 fake new episodes. The approximate family key removes
   most of them. The remaining ~42 are salt and hydrate wording ("Lidocaine HCl
   Anhydrous" vs "Lidocaine HCl"), which the proper UNII/moiety key (D3) should
   remove.
2. **Few dated new starts after 2023.** The archive finds 8 (2024) and 5
   (2026, to October); 2025 can't be dated because of the capture gap. openFDA
   posting dates (4 / 3 / 5) are consistent with this, but they count only
   shortages still listed today, so they are a floor and not a comparison. The
   distinct Current names per year also fell (2022: 170, 2024: 133, 2026: 80).
   Not yet cross-checked against FDA's annual shortage reports to Congress.
3. 2018–2019 captures are sparse (72–87% weekly coverage), so many starts from
   those years are censored.

**Verdict:** labels exist for **about 350 dated episode starts (2014–2026)**,
plus 113 starts known only to within a capture-gap window. Most dated starts
are in 2016–2023, which is where a walk-forward backtest can be evaluated.
That is enough for a careful backtest of a simple model, not for a
data-hungry one.

## 5b. Mapping shortage records to drug families

openFDA shortage field coverage (n = 1,604): `generic_name`, `package_ndc`,
`presentation`, `company_name`, `initial_posting_date` 100%; `dosage_form`
99.4%; `openfda.{product_ndc, substance_name, route, unii}` about 80%;
`shortage_reason` 25%; `discontinued_date` 28%.

- Records **with** an `openfda` block (80%) map straight to UNII + route.
- For the other 20% (n = 320), joining `package_ndc` to the NDC text files
  (current **plus** excluded) finds the product for 96%. 149 of those hits come
  from the *excluded* file, so delisted products matter.
- **Overall, 99.3% of openFDA shortage records map to an NDC product
  automatically.** The 12 misses are mostly non-standard NDC formats (e.g.
  `39822-0355-6`) and could be fixed with normalization.
- **Archived listings have no NDC**, only names. FDA's list name equals the
  openFDA `generic_name` (e.g. "Bupivacaine Hydrochloride Injection"), so
  current names map exactly. Older names need parsing (decisions D5). Not yet
  measured: the share of the 2,090 historical names that map automatically.
  That is the first Phase 2 task.

Example record (abridged):
```json
{"generic_name": "Dextrose Monohydrate 5% Injection", "status": "Current",
 "initial_posting_date": "02/14/2022", "package_ndc": "0264-1510-32",
 "dosage_form": "Injection", "company_name": "B. Braun Medical Inc.",
 "openfda": {"unii": ["LX22YL083G"], "route": ["INTRAVENOUS"],
             "substance_name": ["DEXTROSE MONOHYDRATE"], "spl_set_id": ["60b6d7c3-…"]}}
```

## 5c. Linking NDC labelers to inspection records

Inspections are keyed by **FEI** (one per facility). NDC records name a
**labeler**, which is often not the manufacturer. We tested two routes on
shortage products (`scripts/audit_labeler_fei.py`, `scripts/audit_spl_establishments.py`).

**Route 1: labeler name → registration firm/registrant name → FEI.**
Sample: 50 of the 165 labelers of shortage NDCs (seed 7).
- Normalized exact match: **44%** (22/50). Matching only the first token raises
  this to 66% but is ambiguous (e.g. "AMNEAL" matches 15 FEIs).
- Failure cases (28):
  - **Virtual or contract labelers that don't make the drug and don't register
    a plant:** Avenacy, BluePoint Laboratories, Civica, Meitheal, Solco
    Healthcare (×2 spellings), XGen DJB, Armas, Ingenus, Wilshire, Vertical,
    Woodward, Oryza, Journey Medical, Medexus, Cumberland, Epizyme.
  - **Subsidiary or naming mismatch:** Amneal Pharmaceuticals NY, Viatris
    Specialty, Eugia US, Fosun Pharma USA (×2), Horizon Therapeutics USA, Ipsen
    Biopharmaceuticals, Chartwell RX, Esteve S.A., Alvogen, Upsher-Smith.
- Even a match is weak evidence: "Eli Lilly and Company" matches 10 FEIs, and
  the labeler's own plants may not make *this* product.

**Route 2 (better): product label (SPL) establishment section → DUNS → FEI.**
SPLs on DailyMed list the establishments that manufacture each product (DUNS +
operation). Example: Meitheal's azacitidine injection SPL names **Kindos
Pharmaceuticals** (DUNS 529111185) as manufacturer.
- In a sample of 30 shortage SPLs, **63% list ≥ 1 establishment**, and **27 of
  29 establishment DUNS (93%) resolve to an FEI** via DECRS. So 57% of products
  get an FEI-linked manufacturer.
- The other 37% have no establishment section (older or compact labels).
- **Point-in-time caveat:** DailyMed serves the *current* label. Past
  manufacturers need SPL version history (DailyMed keeps older versions;
  feasibility not yet tested).

**Verdict:** a reliable labeler→inspection link needs Route 2 plus inspection
data keyed by FEI. Route 1 is a fallback at about 44% coverage.

## 5d. Base rate and what it means for precision@k

- **Candidate families:** 3,947 current Rx (ingredient + coarse form + route)
  families; 2,068 of them have a generic (ANDA) product; 46% are single-source.
- **New shortages per 90 days:** in the 26 of 47 quarters that captures fully
  cover, a mean of 9.7 and a median of 8 uncensored starts (range 0–46; the 46
  is the 2023-09 rename artifact). Recent years are lower (~1–3 per quarter in
  2024–2026).
- **Base rate per family per 90 days ≈ 0.25%** (roughly 1 in 400).
  - Random precision@k is about 0.25% for any k.
  - **Ceiling** precision@k (all ~10 positives ranked top): k=25 → 39%, k=50 →
    19%, k=100 → 10%. In 2024–2026 the ceilings are 3–4× lower.
  - For context: the Berkeley project reported ~0.1% precision after
    calibration on a 4-week horizon. We don't know their family universe or
    exact precision definition, but that is the same order of magnitude as a
    random list at a base rate like ours (scaled to 4 weeks, about 0.08%).

**Implications:**
1. Headline metrics should be **recall@k and lift over random**, with precision@k
   reported against its ceiling. With ~10 positives per quarter, per-quarter
   precision is very noisy, so we should pool over the backtest and use
   bootstrap CIs.
2. We should consider restricting the universe to **multi-source generics and
   sterile injectables**, where most shortages occur. That raises the base rate
   without hiding anything, as long as the restriction is stated.
3. The falling recent rate means a model trained on 2017–2023 will be
   miscalibrated for 2025+. Calibration needs time-aware recalibration.

## 5e. Unavailable, rate-limited or unusable sources, and workarounds

1. **FDA "current and historical" shortage xlsx: 404.** Replaced by Wayback
   captures (§5a). From now on our own daily snapshots remove that dependency.
2. **Wayback capture gaps:** 2024-09-06 → 2025-09-05 (364 days), plus 22
   other gaps longer than 3 weeks (up to 117 days) in 2014–2016 and 2018–2021. Possible fixes:
   (a) query alternate URL forms (`drugshortages/`, `Drugshortages.cfm`, `http://`)
   in the CDX index; (b) date episodes from the openFDA `initial_posting_date`
   when the episode is still listed; (c) archived per-drug detail pages may show a posting date that
   fills gaps (unverified). None of these has been tried yet.
3. **FDA Data Dashboard (inspections, citations, compliance actions):** the
   "Entire Dataset" links (`InspectionsDataset.xlsx`) return 404 when fetched
   directly. The real export runs inside a Qlik JavaScript app. The REST API
   needs an Authorization-User/Key pair issued through FDA's **OII Unified
   Logon** account. That is free, but it means *creating an account*, which the
   project owner must do. Options:
   - **(a, recommended)** The owner requests a free API key; we store it as
     `FDA_DD_USER` / `FDA_DD_KEY` env vars and in GitHub Actions secrets.
   - (b) The owner downloads "Entire Inspections Dataset" by hand once from the
     dashboard, and we ingest that file. This doesn't work for nightly updates.
   - (c) Use Wayback captures of the dashboard export: checked, and all captures
     are 404 or empty. Not viable.
4. **Warning letters:** the fda.gov table's xlsx export ignores `page`, date
   and office filters and always returns the same 1,000 rows (posted dates
   2021-03 → 2026-04; most are tobacco/food). No FEI. Workarounds: the Data
   Dashboard *compliance actions* dataset (FEI-keyed, needs the same key as
   item 3), or snapshot the export daily from now on (implemented).
5. **NADAC:** bulk download speed is about 170 KB/s, and each yearly CSV is
   ~100–200 MB (2013 file: 9.6 MB for 5 weeks), so the full history takes hours.
   The fetcher skips past years already downloaded, so the job resumes. The
   DKAN query API is fast for filtered pulls. **Coverage problem:** in the
   latest NADAC week (30,167 NDCs), only **37% of the 233 current shortage
   families have any priced NDC, and only 24% of injectable shortage families**
   (n=110), versus 61% of tablet families. NADAC surveys retail pharmacies, so
   price erosion is a usable signal for oral generics but mostly missing for
   hospital injectables.
6. **Recalls (openFDA enforcement):** use `report_date` (when it was published),
   not `recall_initiation_date`. The median lag between them is 47 days, so
   using initiation date would leak. Only 17% of recalls carry an openFDA NDC
   block; the rest need NDC parsing from `product_description` / `code_info`.
7. **NDC history:** `ndc_excluded` gives start and end marketing dates, but
   these are self-reported, may be backfilled, and products purged before the
   file existed are missing. Manufacturer counts for early years will therefore
   be undercounts. This needs a sensitivity check in Phase 2.
   **Correction (Phase 2):** the excluded file has *no* ingredient, dosage
   form, route or product type. Those columns are 0% filled for all 207,190
   rows. It only names the NDC, labeler, brand, marketing category,
   application number and dates. Phase 1 overstated what it gives on its own.
   The fix is in Phase 2 below: link through the application number to
   Drugs@FDA.
8. **openFDA API date-range queries:** an `initial_posting_date:[… TO …]`
   search failed in testing. That was a curl globbing mistake (`[` needs `-g`),
   not an API problem. Dates in this endpoint are `MM/DD/YYYY` strings.

---

# Phase 2 update (2026-10-29): families, labels, weekly table

All numbers below come from `make build` followed by `scripts/audit_listing_mapping.py`
and `scripts/audit_phase2.py`.

## Drug families (D3 as built)

- Key: `sorted active-moiety UNIIs | coarse form | route group`, e.g.
  `B76N6SBZ8R|INJECTION|PARENTERAL` (gemcitabine injection).
- Ingredient names resolve to UNIIs through 987k GSRS names and synonyms, then to an
  active moiety through 32.6k GSRS ACTIVE MOIETY links. 99.9% of current Rx NDC products
  resolve every ingredient.
- Dosage forms map through a reviewed crosswalk:
  `src/rxradar/transform/data/dosage_form_groups.csv`, 149 NDC and Drugs@FDA
  spellings mapped into 29 groups.
- **Delisted products:** 85,235 delisted Rx products (ANDA/NDA/BLA) get a family through
  their application number in Drugs@FDA.
  - 91.6% resolve overall: ANDA 98.3%, NDA 94.8%, authorized generics 98.3%, BLA 6.7%.
  - BLAs mostly aren't in Drugs@FDA; the Purple Book would be the fix.
  - 407 applications hold several families and are left unassigned rather than guessed.
- The result is **3,938 families** (3,179 with a currently listed product) across
  142,144 current and delisted products.

Data surprises found while building this:
1. **GSRS lists metabolites as "active moieties".** For example, gemcitabine lists its
   di- and triphosphate. 194 substances have several moiety links. Rule: a substance that
   is anyone's moiety is its own moiety (`resolve_moiety_links`).
2. **Electrolytes collapse to bare ions.** Potassium chloride, acetate and phosphate all
   have moiety "potassium ion". Salts whose moiety is an ion keep their own UNII, so these
   stay separate families.
3. **Spellings differ between NDC and Drugs@FDA:** `RESPIRATORY (INHALATION)` vs
   `INHALATION`, `INJECTABLE` vs `INJECTION, SOLUTION`, `ORAL-28`. Drugs@FDA also lists
   premixed IV bags as `SOLUTION` with an intravenous route. All of these are normalized.

## Archived listing names → families (D5)

| | names | |
|---|---|---|
| Distinct listing names, 2014–2026 | 2,090 | |
| Mapped via openFDA `generic_name` | 210 | exact NDC link |
| Mapped by parsing ingredients + form/route words | 1,623 | incl. 13 where the release type was relaxed ("Desvenlafaxine Tablets" when only ER tablets exist) |
| **Mapped overall** | **87.7%** | |
| Mapped among the 660 names ever listed as Current | **90.3%** | **92.9%** weighted by Current listing-weeks |

- **Spot check:** in 30 random parsed mappings, the ingredient, form and route were
  plausible for all 30. Checked by GSRS synonym, e.g. DOZIC = haloperidol, 3TC = lamivudine.
- **Bugs fixed during the check:**
  - Unit tokens matched GSRS synonyms: "mg" is magnesium and "ER" is erbium, which turned
    "Epinephrine 0.1 mg/mL" into a two-ingredient drug.
  - Diluents ("… in 0.9% Sodium Chloride") added an ingredient the NDC record doesn't list.
- **Unmapped:** 64 Current names, listed in `docs/unmapped_listing_names.csv`. They are
  mostly multi-ingredient product classes (multivitamin infusion, peritoneal dialysis and
  CRRT solutions, amino acids, IV fat emulsions, trace elements), radiopharmaceuticals,
  and products discontinued before the NDC history. The reviewed override file
  (`data/listing_name_overrides.csv`) is empty so far.

## Family episodes and labels (D4 as built)

- **Episodes:** 429 at family level, 274 with a dated start across 239 families.
  - 4 of those are single-capture blips (3 on the 2023-09-18 rename day, e.g. a
    one-week "Metronidazole Solution"). They are not counted as onsets, which leaves
    **270 dated onsets**.
  - Median duration is 553 days; p90 is 2,045.
- **Dated starts per year:** 2014: 3, 2015: 15, 2016: 19, 2017: 34, 2018: 30, 2019: 11,
  2020: 40, 2021: 29, 2022: 40, 2023: 42, 2024: 6, 2025: 0 (capture gap), 2026: 5.
  - Compared with the Phase 1 name-keyed counts, 2023 drops from 78 to 42, which shows
    the family key absorbs most of the rename.
  - About 10 starts on 2023-09-18 remain. Several are real 2023 shortages
    (methylphenidate tablets, chewable lisdexamfetamine, ketorolac tablets); a few may be
    rename artifacts.

## Weekly family table (`data/derived/panel.parquet`)

- 1,907,383 rows = 3,767 families × 644 weeks (2014-06 → 2026-10), about 2,990 families
  per week.
- Usable rows have a known horizon and are not in shortage at *t*:

| | usable rows | positive rows | rate | share of positives |
|---|---|---|---|---|
| **y90**, all families | 1,034,625 (54%) | 2,481 | 0.240% | 100% |
| injectable | 252,743 | 1,454 | 0.575% | 59% |
| multi-source generic | 462,840 | 1,463 | 0.316% | 59% |
| generic **or** injectable | 641,536 | 2,063 | 0.322% | 83% |
| neither | 393,089 | 418 | 0.106% | 17% |
| **y30**, all families | 1,237,439 (65%) | 1,028 | 0.083% | |

- Positive *rows* are not independent events. Each onset labels about 13 consecutive
  weeks at 90 days. Evaluation must count events (onsets caught), not rows, and
  bootstrap over onsets.
- Usable 90-day rows by year are sparse in 2014–15, 2018 and 2019 (capture gaps), and
  2025 has no positives. The backtest's evaluation years are realistically 2016–2017
  and 2020–2023, plus 2024 and 2026 as a thin recent check.

**Point-in-time caveats:**
- Universe membership uses self-reported NDC marketing dates, which may be backfilled
  (see `transform/panel.py`). This affects which rows exist, not labels. Phase 3
  manufacturer counts need a stricter treatment.
- Row exclusion (`in_shortage_now`) reads only the capture at *t*; this is tested.

## Wayback gap fill

- Alternate URL forms of the list page (`drugshortages/`, `Drugshortages.cfm`,
  query-string variants) are searched inside each capture gap
  (`fda_shortage_archive.find_gap_captures`).
- On 2026-10-29 the CDX server answered almost every request with HTTP 503, so the search
  is running at one request per 5 s and **no captures have been recovered yet**. It's
  resumable; rerun with the snippet in the Phase 2 report.

## FDA Data Dashboard

- The client (`ingest/fda_dashboard.py`) is written from the published OpenAPI spec and
  docs: POST `api-datadashboard.fda.gov/v1/<endpoint>`, 5,000 rows per page, statuscode
  400 = success.
- It is **untested against the live API** until `FDA_DD_USER` / `FDA_DD_KEY` exist.
- Features must date inspection outcomes by `FMD145Date` (final classification letter),
  not `InspectionEndDate`.

## Name mapping update (2026-11-04)

44 reviewed overrides, each with a reason, in
`src/rxradar/transform/data/listing_name_overrides.csv`. Only clear matches were mapped.

| | before | after |
|---|---|---|
| Names ever listed as Current that map | 90.3% | **96.7%** (96.5% by listing-weeks) |
| Dated onsets | 274 | **287** across 252 families |
| Starts on the 2023-09-18 rename date | 10 | 6 |

- **Why dated onsets were 274 here and 270 earlier:** two gap-fill captures arrived after
  the Phase 2 count.
- **Left unmapped on purpose (20 names):** multi-product classes (multivitamin infusions,
  dialysis and CRRT solutions, amino acids, trace elements), ambiguous names (bare
  "Diltiazem Hydrochloride", "Phosphate Injection Products", "Sterile Water"), and products
  with no matching family (Liotrix, Erwinaze, Tc-99m sulfur colloid).
- **FDA test data:** "TEST ADD REASON SHORTAGE" appeared on FDA's public list for 29 weeks
  in 2022–23. It is excluded.
- **Class listings would need a new design:** one listing covering several families can't
  be expressed with today's one-name-one-family mapping.

## DailyMed label history and factory links (2026-11-04)

Run: 7,351 labels chosen from 2,128 generic or injectable families (up to 5 labelers each),
30,085 label versions, no failed labels, 4 retried connection errors. It took about 25
hours, about 12 of them running time; the rest was the Mac sleeping.

- 7,346 labels had a set ID. 21,661 of 30,085 versions (72%) listed an establishment DUNS.
- 1,383 of 1,925 distinct DUNS (72%) resolved to an FEI. The rest may be closed plants or
  plants missing from today's registration files.
- Coverage is lower than the pilot suggested because the pilot's denominator was the
  sampled pool, while this one is every family in the panel:

| Week | Panel families | With a label | With a factory | With an inspected factory |
|---|---|---|---|---|
| 2017-01 | 2,685 | 49% | 35% | 35% |
| 2019-01 | 2,860 | 54% | 42% | 41% |
| 2021-01 | 3,004 | 57% | 47% | 46% |
| 2023-01 | 3,120 | 59% | 51% | 51% |
| 2026-01 | 3,340 | 60% | 53% | 52% |

Current site: 1,803 of 3,393 families have inspection facts, including 99 of the top 100.

Staying current: inspections refresh weekly when the Data Dashboard secrets are set. Labels
refresh in rotation, 200 a night, so a full pass takes about 37 days. The first run on
GitHub's servers (2026-11-05) checked 200 labels with no failures and found 0 new versions,
so DailyMed allows it. Adding a new version from there hasn't happened yet, and the weekly
inspection refresh first runs inside the nightly job on Monday. The DUNS map can't refresh there, because FDA blocks those files, so
new factories stay unmatched until it's rebuilt from a home connection.
