# Decisions log

Each entry says what was decided (or proposed), why, and what we gave up.
Entries marked **PROPOSED** need the project owner's sign-off before code
depends on them.

---

## D1. Repo layout: `src/` package (decided)

The brief proposed top-level `rxradar/ingest`, `rxradar/transform`, and so
on. We use the same sub-packages under `src/rxradar/`. With a src layout the
tests import the *installed* package, not the working directory, so import
errors and missing files surface in CI instead of hiding locally. Throwaway
audit analyses live in `scripts/` and are not part of the package.

## D2. Using Internet Archive copies of FDA's shortage page — DECIDED 2026-10-29 (label source)

FDA publishes no shortage history: openFDA and FDA's own page list only
current shortages, discontinuations and a few recently resolved ones. The
Wayback Machine holds about daily copies of FDA's public list page since
2014-06. These are verbatim copies of a government page, not a third-party
shortage list (we do **not** use ASHP or any licensed list). On 2026-10-29 the
owner approved investigating this source, and after the Phase 1 audit approved
it as the label source.

Cost: our label history depends on a non-government archive's capture
schedule, including one gap from 2024-09 to 2025-09. Mitigation: from now on
we snapshot openFDA and FDA's page ourselves every day, so future labels
don't depend on the Archive.

## D3. Drug-family key — DECIDED 2026-10-29: option A, salts collapsed to active moiety

**Recommendation: `sorted active-ingredient UNIIs + coarse dosage form + route`.**
For example `{LX22YL083G}|INJECTION|INTRAVENOUS` for dextrose injection.

| Option | Pros | Cons |
|---|---|---|
| **A. UNII set + coarse form + route** (recommended) | Stable IDs, unaffected by salt-name spelling; maps 99% of openFDA shortage records via NDC; ~3,800 Rx families today | FDA's archived list names ("Digoxin Injection") have no NDC, so they need a name-to-family lookup (see D5); "coarse form" requires a hand-made form crosswalk (~150 NDC dosage form strings into ~25 groups) |
| B. UNII set + exact NDC dosage form + route | No crosswalk to maintain | Splits real families ("TABLET" vs "TABLET, FILM COATED") into separate families. FDA's shortage list is not that granular, so labels would be ambiguous |
| C. Add strength or concentration | Matches how pharmacists see a shortage | FDA lists shortages at ingredient+form level, so labels don't exist at strength level; 5–10× more families and even rarer positives |
| D. RxNorm ingredient + dose form group (RxCUI) | Clinically clean grouping | Adds a dependency (NLM RxNorm, also public); mapping NDC→RxCUI over time is itself a point-in-time problem |

"Coarse form" means: injection (all parenteral liquids/powders), tablet
(including film-coated, chewable, ER kept **separate**: ER is its own
group), capsule (ER separate), oral liquid, ophthalmic, otic, topical,
inhalation, and so on. The crosswalk will be a reviewed CSV in the repo.

Open question for the owner: should salts collapse to the parent moiety
(for example, morphine sulfate and morphine hydrochloride as one family)?
**Recommendation: yes, collapse to the active moiety.** FDA's shortage
listings already mix salts, and splitting them would scatter one shortage
over several families.

## D4. Label definition — DECIDED 2026-10-29: 90-day primary horizon, 30-day also reported

**Recommendation:** for family *f* and week *t*, `y = 1` if a **new** FDA
shortage episode for *f* starts in `(t, t + 90 days]`. A new episode means
*f* is listed as **Current** in a capture after not being listed as Current
for at least 8 weeks. Weeks where *f* is already in an episode are
**excluded** from training (risk-of-onset model), not labelled 0.

Choices and tradeoffs:

1. **Horizon 90 days vs 4 weeks.** 90 days gives ~3× more positives per row
   and an actionable lead time; 4 weeks is closer to the Berkeley baseline.
   *Recommendation: 90 days primary, and also report 30 days for comparison
   with prior work.*
2. **Status included as an onset.** Only "Current". "To Be Discontinued" is a
   different event (a firm exits; the family may stay well supplied).
   *Recommendation: Current only for the label; discontinuations become a
   feature (a supply exit signal).*
3. **Merge gap 8 weeks.** Shorter gaps turn page flicker into fake new
   episodes. Longer gaps hide real recurrences. The audit reports counts at
   4, 8 and 26 weeks so we can see the sensitivity.
4. **Censored starts** (first capture in 2014-06; after the 2024-09→2025-09
   gap) are not used as positives. Weeks whose horizon overlaps a capture gap
   longer than 3 weeks are dropped from evaluation, because their label is unknown.
5. **Exclude rows during an episode.** Otherwise the model learns "it is in
   shortage now", which is useless for early warning and inflates metrics.
   **Point-in-time rule:** whether row *t* is excluded must depend only on
   whether the family is listed as Current *in the capture at t*, not on the
   merged episode span. The 8-week merge looks ahead (a reappearance decides
   that the weeks in between were "in episode"), so it may only shape labels,
   never row selection or features.

## D5. Mapping archived listing names to families — PROPOSED

FDA's list names (e.g. "Bupivacaine Hydrochloride Injection") equal the
openFDA `generic_name` field. Plan:
1. Exact-name lookup from every openFDA shortage snapshot (name → NDCs →
   UNIIs/form/route).
2. For names never seen in openFDA (old episodes): parse into ingredient words
   + form words and match against NDC substance names (current + excluded
   products). Report unmatched names; hand-curate if fewer than ~100.

## D6. Inspections and compliance data source — DECIDED 2026-10-29: option (a)

FDA Data Dashboard inspection/compliance exports can't be downloaded
programmatically without credentials (see data_audit.md §5e). The owner is
requesting a free API key. It will be read from the `FDA_DD_USER` /
`FDA_DD_KEY` environment variables and never committed.

## D7. Scoring universe — PROPOSED

**Recommendation:** build the weekly panel over **all** Rx families, tagging
each family-week with `multisource_generic` and `injectable` flags (known as
of week *t*). We report metrics on both the full universe and the
generics+injectables subset, and pick the headline once the backtest shows
where the signal is. Restricting the universe early would hide whether the
model just learns "injectables are risky".

## D3/D4 implementation notes (Phase 2, 2026-10-29)

Rules added while building D3/D4. Each covers a data quirk, not a change of
intent:
- **Route group:** every injectable collapses to `PARENTERAL` (IV vs IM;IV vials
  are one family, as on FDA's list). Other routes keep their NDC route set.
- **Electrolytes are not collapsed** to their bare-ion moiety. Potassium
  chloride ≠ potassium acetate ≠ potassium phosphate.
- **GSRS metabolite links are ignored** when the substance is itself a moiety
  (gemcitabine stays gemcitabine).
- **Delisted products** get a family only when their application holds a single
  family, or the brand name settles it. Otherwise they are left unassigned.
- **Single-capture blips** (Current in exactly one capture, then gone) are not
  onsets. There are 4, 3 of them on the 2023-09-18 rename day.
- **Listing-name mapping never guesses.** Ambiguous names stay unmapped and
  are listed in `docs/unmapped_listing_names.csv`. Human decisions go in
  `src/rxradar/transform/data/listing_name_overrides.csv` (empty so far).

## D8. Evaluation unit — PROPOSED for Phase 3

Positive family-weeks are not independent: each onset labels ~13 weeks at
90 days. **Recommendation:** report event-level metrics (share of onsets
flagged in the top-k at least N weeks ahead, plus lead time) alongside
row-level PR-AUC, with bootstrap CIs resampled over onsets.

## D8 revised (Phase 3, 2026-10-29): headline metric is fixed-lead recall

D8 originally proposed "onset recall@k = family in the top-k in *any* week of
the 90 days before onset". In the first backtest a **random ranking beat
every model on that metric** (0.168 vs 0.130 at k=50). It rewards rankings
that churn: a random list gets ~13 independent tries, while a model that
ranks a family consistently either flags it or doesn't. `tests/test_metrics.py`
demonstrates this. The headline is now **recall@k at a fixed lead**: is the
family in the top-k in the last scored week at least 4 / 8 / 12 weeks before
the onset? Random scores ≈ k / N on that, as it should. The any-window number
stays in the report, labelled as flawed. Weekly-precision CIs now resample
13-week blocks, because neighbouring weeks are strongly correlated.

## D9. Live track record rules — DECIDED 2026-11-03, before any live result existed

Backtests can flatter a model. The track record scores predictions frozen before
their outcomes were known. These rules are fixed now; any later change is added
here as a dated amendment, and old results stay published under the old rule.

**Freezing**
- The first nightly run that sees a new week saves every ranked family's risk,
  rank and band, the rule baseline's rank, the families excluded as already in
  shortage, the model hash and the data dates.
- Files live on the `predictions` branch as `<week>.json.gz`. A file that exists
  is never overwritten. Git history is the public timestamp.
- A prediction's **anchor** is the UTC date it was frozen, not its week label.
- Nothing is backfilled. Tracking starts with the first frozen week.

**Outcome.** A dated onset: a family listed as Current on FDA's list after at
least 8 observed weeks off it, with no capture gap over 21 days just before it
(the backtest rule, decisions D4). Onsets seen in one capture and then gone are
page glitches and are not counted.

**Headline: onset recall@k at a 4-week lead, k = 50 and 100.** For each onset,
take the latest frozen week whose anchor is 28–90 days before the onset. It's a
hit if the family's rank was ≤ k. A family that wasn't ranked that week is a miss.
- A family in that week's excluded list is skipped (it was already in shortage).
- Onsets with no frozen week in that window are "too soon to score" and are
  left out of the denominator.

**Weekly precision@k.** For a frozen week whose 90 days have passed: the share of
its top-k families with an onset in (anchor, anchor + 90 days].
- Weeks still inside their 90 days are "pending".
- Weeks whose window has a capture gap over 21 days are "unknown", not misses.

**Comparisons.** Random (expected recall k / number of ranked families; expected
precision = the week's positive share) and the hand-written backtest rule.

**Reporting.** Every scored onset is listed, hit or miss. Live results are never
merged with backtest results. Results by risk band appear only after 30 scored
onsets, because fewer than that would be mostly noise.

### D9 amendment, 2026-11-04: better name mapping, before any result

44 reviewed overrides (`transform/data/listing_name_overrides.csv`) now map shortage
names that previously had no family, including two non-drug rows that are excluded.
This changes which onsets exist, so it's recorded here. No week had been scored, and
the one frozen file (2026-11-02) is unchanged. The scoring rules are unchanged. Families
that turned out to be already in shortage, such as streptozocin, can't produce a false
onset: their episodes began long before tracking did.

## D10. Dating FDA inspection outcomes — DECIDED 2026-11-04 (Option B)

The Data Dashboard shows final outcomes but not when each became public.
`FMD145Date` (final classification letter) is the best evidence, but it is filled on
only 15.5% of failed (OAI) drug-quality inspections and about half of the others, so
requiring it would drop most failures.

An inspection outcome counts from the first of these that applies:
1. `FMD145Date` + 30 days.
2. Otherwise `InspectionEndDate` + the 90th percentile of the dated delay for that
   outcome: **OAI 877 days, VAI 292, NAI 197** (dated results, 0–1,500 days).

Warning letters, injunctions and seizures count from `ActionTakenDate` + 30 days.
Only Drug Quality Assurance inspections are used.

Rejected: dropping undated results (loses 2,146 of 2,541 failures), and the median
delay (about half of results would count before they were public).

Limit: the OAI delay rests on only 362 dated results, which may not be typical. It's
a safeguard, not a measurement of each result's publication date.
