"""FDA Data Dashboard API: inspection classifications and compliance actions.

Why: these are the supply-side signals that set RxRadar apart. Facility
inspections ending in OAI/VAI, and warning letters and other compliance
actions, are keyed by FEI, which we link to products through SPL
establishments and the registration file (docs/data_audit.md §5c).

Access needs a free credential pair requested by the project owner through
FDA's OII Unified Logon (docs/decisions.md D6). It is read from the
environment, never from code:
    FDA_DD_USER  -> Authorization-User header (the registered email)
    FDA_DD_KEY   -> Authorization-Key header

API facts from https://datadashboard.fda.gov/oii/api/ddapi.json and its docs
page (2026-10-29): POST https://api-datadashboard.fda.gov/v1/<endpoint>
with a JSON body {start (1-based), rows (max 5000), sort, sortorder, filters,
columns, returntotalcount}.

**Untested against the live API:** written from the spec before credentials
existed. Unit tests use a mocked transport.

Point-in-time note: the dashboard only shows inspections whose classification
is final. ``FMD145Date`` (date the final classification letter was sent) is
the earliest the outcome could have been public; features must use it, not
``InspectionEndDate``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

from rxradar.http import USER_AGENT
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

BASE_URL = "https://api-datadashboard.fda.gov/v1/"
PAGE_ROWS = 5000
STATUS_SUCCESS = 400
STATUS_NO_RESULTS = 412
ENDPOINTS = {
    "inspections_classifications": {
        "sort": "InspectionID",
        "filters": {"ProductType": ["Drugs"]},
        "columns": [
            "FEINumber", "LegalName", "City", "StateCode", "CountryCode", "InspectionID",
            "InspectionEndDate", "FiscalYear", "Classification", "ClassificationCode",
            "ProjectArea", "ProductType", "PostedCitations", "FMD145Date",
        ],
    },
    "compliance_actions": {
        "sort": "FEINumber",
        "filters": {"ProductType": ["Drugs"]},
        "columns": [
            "FEINumber", "LegalName", "City", "StateCode", "CountryCode", "ProductType",
            "Center", "ActionType", "ActionTakenDate", "FiscalYear", "CaseInjunctionID",
        ],
    },
}  # fmt: skip


class MissingCredentials(RuntimeError):
    pass


def credentials() -> tuple[str, str]:
    user, key = os.environ.get("FDA_DD_USER"), os.environ.get("FDA_DD_KEY")
    if not user or not key:
        raise MissingCredentials(
            "Set FDA_DD_USER and FDA_DD_KEY (free key from FDA OII Unified Logon; "
            "see docs/decisions.md D6)."
        )
    return user, key


def fetch_endpoint(
    endpoint: str,
    user: str,
    key: str,
    transport: httpx.BaseTransport | None = None,
    min_interval: float = 1.0,
) -> list[dict]:
    """Page through one endpoint and return all result rows."""
    spec = ENDPOINTS[endpoint]
    headers = {
        "Authorization-User": user,
        "Authorization-Key": key,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    rows: list[dict] = []
    with httpx.Client(timeout=300, transport=transport, headers=headers) as client:
        start = 1
        while True:
            body = {
                "start": start,
                "rows": PAGE_ROWS,
                "sort": spec["sort"],
                "sortorder": "asc",
                "returntotalcount": True,
                "filters": spec["filters"],
                "columns": spec["columns"],
            }
            resp = client.post(BASE_URL + endpoint, json=body)
            resp.raise_for_status()
            payload = resp.json()
            status = payload.get("statuscode")
            # Per the DDAPI docs, statuscode 400 means "Success." and 412 "No
            # results found."; 401-419 are request errors.
            if status == STATUS_NO_RESULTS:
                break
            if status != STATUS_SUCCESS:
                raise RuntimeError(f"{endpoint}: statuscode {status}: {payload.get('message')}")
            page = payload.get("result") or []
            rows.extend(page)
            total = payload.get("totalrecordcount")
            log.info("%s: %s/%s rows", endpoint, len(rows), total)
            if len(page) < PAGE_ROWS or (total is not None and len(rows) >= total):
                break
            start += PAGE_ROWS
            time.sleep(min_interval)
    return rows


def ingest(transport: httpx.BaseTransport | None = None) -> Path:
    user, key = credentials()
    out = raw_snapshot_dir("fda_dashboard")
    for endpoint in ENDPOINTS:
        rows = fetch_endpoint(endpoint, user, key, transport=transport)
        (out / f"{endpoint}.json").write_text(json.dumps(rows))
    return out
