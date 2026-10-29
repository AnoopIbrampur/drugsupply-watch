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

## D2. Using Internet Archive copies of FDA's shortage page — PROPOSED (approved for investigation 2026-10-29; confirm for labels)

FDA publishes no shortage history: openFDA and FDA's own page list only
current shortages, discontinuations and a few recently resolved ones. The
Wayback Machine holds about daily copies of FDA's public list page since
2014-06. These are verbatim copies of a government page, not a third-party
shortage list (we do **not** use ASHP or any licensed list). On 2026-10-29 the
owner approved investigating this source. Using it as the label source still
needs their confirmation.

Cost: our label history depends on a non-government archive's capture
schedule, including one gap from 2024-09 to 2025-09. Mitigation: from now on
we snapshot openFDA and FDA's page ourselves every day, so future labels
don't depend on the Archive.

## D3. Drug-family key — PROPOSED

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

## D4. Label definition — PROPOSED

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

## D6. Inspections and compliance data source — OPEN (needs owner action)

FDA Data Dashboard inspection/compliance exports can't be downloaded
programmatically without credentials (see data_audit.md §5e). Options are in
the audit.
