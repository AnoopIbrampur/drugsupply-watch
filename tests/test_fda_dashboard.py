"""Data Dashboard client tests with a mocked API (no credentials needed)."""

import json

import httpx
import pytest

from rxradar.ingest import fda_dashboard


def test_missing_credentials_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("FDA_DD_USER", raising=False)
    monkeypatch.delenv("FDA_DD_KEY", raising=False)
    with pytest.raises(fda_dashboard.MissingCredentials):
        fda_dashboard.credentials()


def test_paging_headers_and_body(monkeypatch):
    monkeypatch.setattr(fda_dashboard, "PAGE_ROWS", 2)
    monkeypatch.setattr(fda_dashboard.time, "sleep", lambda s: None)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.headers["Authorization-User"], body["start"], body["rows"]))
        assert body["filters"] == {"ProductType": ["Drugs"]}
        rows = {1: [{"FEINumber": "1"}, {"FEINumber": "2"}], 3: [{"FEINumber": "3"}]}
        return httpx.Response(
            200,
            json={"statuscode": 400, "totalrecordcount": 3, "result": rows[body["start"]]},
        )

    out = fda_dashboard.fetch_endpoint(
        "inspections_classifications", "me@example.com", "k", transport=httpx.MockTransport(handler)
    )
    assert [r["FEINumber"] for r in out] == ["1", "2", "3"]
    assert seen == [("me@example.com", 1, 2), ("me@example.com", 3, 2)]


def test_no_results_status_ends_paging():
    def handler(req):
        return httpx.Response(200, json={"statuscode": 412, "message": "No results found."})

    out = fda_dashboard.fetch_endpoint(
        "compliance_actions", "u", "k", transport=httpx.MockTransport(handler)
    )
    assert out == []


def test_error_status_raises():
    def handler(req):
        return httpx.Response(200, json={"statuscode": 401, "message": "Not Authorized."})

    with pytest.raises(RuntimeError, match="401"):
        fda_dashboard.fetch_endpoint(
            "compliance_actions", "u", "k", transport=httpx.MockTransport(handler)
        )


def test_check_connection_reports_counts_and_never_the_key(monkeypatch):
    monkeypatch.setenv("FDA_DD_USER", "me@example.com")
    monkeypatch.setenv("FDA_DD_KEY", "SECRET-KEY")

    def handler(req):
        assert req.headers["Authorization-Key"] == "SECRET-KEY"
        return httpx.Response(
            200,
            json={"statuscode": 400, "totalrecordcount": 7, "result": [{"FEINumber": "1"}]},
        )

    report = fda_dashboard.check_connection(transport=httpx.MockTransport(handler))
    assert report["inspections_classifications"]["total_records"] == 7
    assert report["compliance_actions"]["columns_returned"] == ["FEINumber"]
    assert "SECRET-KEY" not in json.dumps(report)
