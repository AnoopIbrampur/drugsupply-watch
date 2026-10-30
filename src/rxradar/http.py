"""Shared HTTP client for every source.

Why one client: every source we use is a free public service run by a
government agency or a non-profit archive. We owe them politeness (rate
limits), and we owe ourselves reproducibility (a disk cache so re-running a
pipeline step never re-downloads). Centralising this keeps each ingest module
down to "which URLs, and where do the bytes go".
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "rxradar/0.1 (non-commercial research; https://github.com/anoopibrampur/rxradar)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
# accessdata.fda.gov sends clients it treats as bots to this page; seen from
# GitHub Actions runners on 2026-10-30.
BLOCK_MARKER = "abuse-detection-apology"


class BlockedError(RuntimeError):
    """The server refused us as automated traffic. Never retried."""


class PoliteClient:
    """httpx wrapper with a minimum gap between requests, retries with
    exponential backoff, and an optional content-addressed disk cache.

    The cache is keyed by URL (plus params). It is meant for immutable
    resources such as Wayback captures; for "latest" endpoints pass
    ``use_cache=False`` so we always see today's data.
    """

    def __init__(
        self,
        min_interval: float = 0.5,
        max_retries: int = 8,
        timeout: float = 120.0,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.cache_dir = cache_dir
        self._last_request = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    def _cache_path(self, url: str, params: dict | None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        return self.cache_dir / hashlib.sha256(key.encode()).hexdigest()[:32]

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get_bytes(self, url: str, params: dict | None = None, use_cache: bool = True) -> bytes:
        """GET a URL and return the body, retrying transient failures."""
        cache = self._cache_path(url, params) if use_cache else None
        if cache is not None and cache.exists():
            return cache.read_bytes()

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("transport error on %s (%s); retrying", url, exc)
            else:
                if BLOCK_MARKER in str(resp.url):
                    # FDA's bot protection redirected us. Retrying (or
                    # disguising the client) would be evasion; stop instead.
                    raise BlockedError(f"{url} redirected to a bot-protection page")
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    if cache is not None:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        cache.write_bytes(resp.content)
                    return resp.content
                if attempt == self.max_retries:
                    resp.raise_for_status()
                log.warning("HTTP %s on %s; retrying", resp.status_code, url)
            time.sleep(min(2**attempt, 120))
        raise RuntimeError("unreachable")

    def get_json(self, url: str, params: dict | None = None, use_cache: bool = False):
        import json

        return json.loads(self.get_bytes(url, params=params, use_cache=use_cache))

    def close(self) -> None:
        self._client.close()
