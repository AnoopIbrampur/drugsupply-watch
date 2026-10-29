"""Turn weekly archive captures of FDA's shortage list into shortage episodes.

An *episode* is a run of consecutive captures in which a listing name has
status Current. Its start date is the capture where it first appears as
Current, which is point-in-time safe: FDA had published it by then.

Two things make this harder than a diff:
- Capture gaps. Wayback captures are not perfectly weekly (one gap is ~1
  year, 2024-09 to 2025-09). If a name is Current right after a long gap, we
  cannot know whether it started during the gap, so the start is flagged as
  ``start_censored`` rather than dated precisely.
- Flicker. Names sometimes drop off for a week and come back (page glitches,
  renames). Absences shorter than ``merge_gap_days`` are merged into one episode.

This module is a Phase 1 prototype used to count episodes for the data audit;
the final label definition is still open (docs/decisions.md).
"""

from __future__ import annotations

import re

import pandas as pd

MAX_CAPTURE_GAP_DAYS = 21
MERGE_GAP_DAYS = 56


def normalize_name(name: str) -> str:
    """Collapse cosmetic differences in listing names across years.

    Drops bracketed brand names ("Paliperidone (INVEGA) ..."), "USP" markers
    and punctuation, and lowercases. It deliberately keeps the dosage form
    words, which are part of the drug family.
    """
    s = re.sub(r"\([^)]*\)", " ", name)
    s = re.sub(r"\bUSP\b", " ", s, flags=re.I)
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return " ".join(s.split())


FORM_GROUPS = [  # first match wins; order matters ("ophthalmic solution" is ophthalmic)
    ("ophthalmic", "OPHTHALMIC"),
    ("otic", "OTIC"),
    ("inhal", "INHALATION"),
    ("nasal", "NASAL"),
    ("spray", "NASAL"),
    ("inject", "INJECTION"),
    ("infusion", "INJECTION"),
    ("vial", "INJECTION"),
    ("irrigat", "IRRIGATION"),
    ("tablet", "TABLET"),
    ("capsule", "CAPSULE"),
    ("suppositor", "RECTAL"),
    ("cream", "TOPICAL"),
    ("ointment", "TOPICAL"),
    ("gel", "TOPICAL"),
    ("topical", "TOPICAL"),
    ("patch", "TRANSDERMAL"),
    ("suspension", "ORAL_LIQUID"),
    ("solution", "ORAL_LIQUID"),
    ("oral", "ORAL_LIQUID"),
]
# Words that describe the form or packaging rather than the ingredient.
_FORM_WORDS = frozenset(
    """
    injection injections injectable tablet tablets capsule capsules solution solutions
    suspension powder powders lyophilized for oral film coated chewable extended delayed
    release orally disintegrating ophthalmic otic inhalation nasal spray topical cream
    ointment gel suppository suppositories irrigation infusion premix premixed
    concentrate sterile single dose multi vial vials kit emulsion liquid syrup elixir
    drops and in with plastic container bag usp per mg ml mcg g units
    unit hcl
    """.split()  # noqa: SIM905 - a word list reads better than a quoted list
)


def form_group(name: str) -> str:
    low = name.lower()
    return next((group for word, group in FORM_GROUPS if word in low), "OTHER")


def approx_family_key(name: str) -> str:
    """Rough ingredient + form-group key from a listing name alone.

    Phase 1 stand-in for the real family key (docs/decisions.md D3/D5), which
    will come from NDC/UNII data. It exists to bridge FDA's 2023-09 renaming,
    e.g. "Amifostine Injection" -> "Amifostine Injection, Powder, Lyophilized,
    For Solution", which would otherwise create fake new episodes.
    """
    s = re.sub(r"\([^)]*\)", " ", name.lower())
    words = re.findall(r"[a-z]+", s)
    ingredients = sorted({w for w in words if w not in _FORM_WORDS and len(w) > 2})
    return " ".join(ingredients) + " | " + form_group(name)


def build_episodes(
    listings: pd.DataFrame,
    max_capture_gap_days: int = MAX_CAPTURE_GAP_DAYS,
    merge_gap_days: int = MERGE_GAP_DAYS,
    key_fn=normalize_name,
) -> pd.DataFrame:
    """Build episodes from rows of (capture_ts, name, status).

    ``key_fn`` maps a listing name to the episode key (default: lightly
    normalized name). Returns one row per episode with: key, start, end (last capture seen as
    Current), start_censored (could have started earlier than observed), and
    ongoing (still Current in the last capture).
    """
    df = listings.copy()
    df["date"] = pd.to_datetime(df["capture_ts"].str[:8], format="%Y%m%d")
    df["key"] = df["name"].map(key_fn)
    captures = sorted(df["date"].unique())
    prev_capture = dict(zip(captures[1:], captures[:-1], strict=True))
    last_capture = captures[-1]

    # Absence is measured in *observed* time: each step between consecutive
    # captures counts at most max_capture_gap_days. Without this, a drug that
    # is Current on both sides of a long capture gap (e.g. 2024-09 -> 2025-09)
    # would be split into two episodes, the second a fake "new" start.
    steps = [0.0] + [
        min((b - a).days, max_capture_gap_days)
        for a, b in zip(captures[:-1], captures[1:], strict=True)
    ]
    observed_clock = dict(zip(captures, pd.Series(steps).cumsum(), strict=True))

    current = df[df["status"] == "Current"].drop_duplicates(["key", "date"])
    episodes = []
    for key, grp in current.groupby("key"):
        dates = sorted(grp["date"])
        start = end = dates[0]
        for d in dates[1:]:
            if observed_clock[d] - observed_clock[end] <= merge_gap_days:
                end = d
                continue
            episodes.append(_episode(key, start, end, prev_capture, max_capture_gap_days))
            start = end = d
        episodes.append(_episode(key, start, end, prev_capture, max_capture_gap_days))

    out = pd.DataFrame(episodes)
    out["ongoing"] = out["end"] == last_capture
    return out.sort_values(["start", "key"]).reset_index(drop=True)


def _episode(key, start, end, prev_capture, max_gap) -> dict:
    prev = prev_capture.get(start)
    # Censored if this is the first capture ever, or the previous capture is
    # too far back to rule out an earlier, unseen start.
    censored = prev is None or (start - prev).days > max_gap
    return {"key": key, "start": start, "end": end, "start_censored": censored}
