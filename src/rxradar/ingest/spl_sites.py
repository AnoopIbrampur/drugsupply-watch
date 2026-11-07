"""Every current Rx label's plants and what each one does, from DailyMed's bulk files.

Why: the plant map needs all labels, and label-by-label fetching (dailymed.py) takes a
second per request. DailyMed publishes all current prescription labels as a few zip
files of about 3 GB each, which GitHub's servers may download. These files hold only
each label's latest version, so they describe today. Past dates still come from
dailymed.py.

Each label lists its plants and, for each, the operations it performs on each NDC
(MANUFACTURE, API MANUFACTURE, ANALYSIS, LABEL, PACK and so on). That is what lets the
simulator count real manufacturing and leave out sites that only test or relabel.

Each zip is deleted once read, so a run needs disk for one part at a time. Output:
data/raw/spl_sites/<date>/sites.parquet, one row per label, plant, operation and NDC.
"""

from __future__ import annotations

import io
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pandas as pd

from rxradar.http import USER_AGENT
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

BASE = "https://dailymed-data.nlm.nih.gov/public-release-files/"
LISTING = "https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm"
PART_NAME = re.compile(r"dm_spl_release_human_rx_part\d+\.zip")
V3 = "{urn:hl7-org:v3}"
DUNS_ROOT = "1.3.6.1.4.1.519.1"
FEI_ROOT = "2.16.840.1.113883.4.82"
COLUMNS = [
    "set_id", "version", "effective", "labeler", "labeler_duns", "plant_duns", "plant_fei",
    "plant_name", "country", "operation", "ndc",
]  # fmt: skip


def _ids(org: ET.Element) -> tuple[str | None, str | None]:
    duns = fei = None
    for i in org.findall(f"{V3}id"):
        if i.get("root") == DUNS_ROOT:
            duns = i.get("extension")
        elif i.get("root") == FEI_ROOT:
            fei = i.get("extension")
    return duns, fei


def parse_sites(xml: bytes) -> list[dict]:
    """One row per plant, operation and NDC. Only real SPL structure is read: a label
    whose author block is missing gives no rows rather than a guess."""
    root = ET.fromstring(xml)
    author = root.find(f"{V3}author")
    if author is None:
        return []
    labeler = author.find(f"{V3}assignedEntity/{V3}representedOrganization")
    if labeler is None:
        return []
    set_id = root.find(f"{V3}setId")
    version = root.find(f"{V3}versionNumber")
    effective = root.find(f"{V3}effectiveTime")
    base = {
        "set_id": None if set_id is None else set_id.get("root"),
        "version": None if version is None else int(version.get("value")),
        "effective": None if effective is None else effective.get("value"),
        "labeler": (labeler.findtext(f"{V3}name") or "").strip(),
        "labeler_duns": _ids(labeler)[0],
    }
    rows = []
    for entity in author.iter(f"{V3}assignedEntity"):
        org = entity.find(f"{V3}assignedOrganization")
        performances = entity.findall(f"{V3}performance")
        if org is None or not performances:
            continue
        duns, fei = _ids(org)
        place = {
            "plant_duns": duns,
            "plant_fei": fei,
            "plant_name": (org.findtext(f"{V3}name") or "").strip(),
            "country": org.findtext(f"{V3}addr/{V3}country/{V3}name")
            or org.findtext(f"{V3}addr/{V3}country"),
        }
        for performance in performances:
            act = performance.find(f"{V3}actDefinition")
            code = None if act is None else act.find(f"{V3}code")
            operation = None if code is None else code.get("displayName")
            ndcs = [
                c.get("code")
                for c in act.iter(f"{V3}code")
                if c is not code and c.get("code")
            ] if act is not None else []  # fmt: skip
            for ndc in ndcs or [None]:
                rows.append({**base, **place, "operation": operation, "ndc": ndc})
    return rows


def label_xmls(part: Path) -> Iterator[bytes]:
    """The label XML inside each per-label zip of a bulk part."""
    with zipfile.ZipFile(part) as outer:
        for name in outer.namelist():
            if not name.endswith(".zip"):
                continue
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                for member in inner.namelist():
                    if member.endswith(".xml"):
                        yield inner.read(member)


def part_names() -> list[str]:
    headers = {"User-Agent": USER_AGENT}
    page = httpx.get(LISTING, headers=headers, timeout=60, follow_redirects=True).text
    names = sorted(set(PART_NAME.findall(page)), key=lambda n: int(re.search(r"\d+", n)[0]))
    if not names:
        raise RuntimeError("DailyMed's download page lists no Rx parts; has it changed?")
    return names


def download(name: str, dest: Path, attempts: int = 4) -> None:
    """Stream to disk: a part is 3 GB, too big to hold in memory."""
    for attempt in range(1, attempts + 1):
        try:
            with httpx.stream(
                "GET", BASE + name, headers={"User-Agent": USER_AGENT},
                timeout=120, follow_redirects=True,
            ) as response:  # fmt: skip
                response.raise_for_status()
                with dest.open("wb") as fh:
                    for chunk in response.iter_bytes(1 << 20):
                        fh.write(chunk)
            return
        except httpx.HTTPError as exc:
            if attempt == attempts:
                raise
            log.warning("%s attempt %d failed: %s", name, attempt, exc)
            time.sleep(60 * attempt)


def read_part(part: Path) -> tuple[pd.DataFrame, int, int]:
    rows, labels, bad = [], 0, 0
    for xml in label_xmls(part):
        labels += 1
        try:
            rows.extend(parse_sites(xml))
        except ET.ParseError:
            bad += 1
    return pd.DataFrame(rows, columns=COLUMNS).drop_duplicates(), labels, bad


def ingest() -> Path:
    out = raw_snapshot_dir("spl_sites")
    frames, total, failed = [], 0, 0
    for name in part_names():
        path = out / name
        download(name, path)
        df, labels, bad = read_part(path)
        path.unlink()
        log.info("%s: %d labels, %d rows, %d unreadable", name, labels, len(df), bad)
        frames.append(df)
        total, failed = total + labels, failed + bad
    result = pd.concat(frames, ignore_index=True)
    result.to_parquet(out / "sites.parquet")
    log.info("all parts: %d labels, %d rows, %d unreadable", total, len(result), failed)
    return out
