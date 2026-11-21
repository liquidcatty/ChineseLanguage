"""Supabase storage for generated stories (PostgREST over HTTPS).

The browser never talks to Supabase directly: FastAPI keeps the secret key on
the server and proxies every read and write.  Configuration comes from the
environment, with a small ``.env`` reader so a local checkout works without
exporting anything:

    SUPABASE_URL=https://<project-ref>.supabase.co
    SUPABASE_KEY=sb_secret_...          # server-side key, never shipped to static/

Optional:

    SUPABASE_STORIES_TABLE=stories      # table name (default ``stories``)
    SUPABASE_TIMEOUT=20                 # request timeout in seconds

Every story lives in one row: the summary columns (``title``, ``level``,
``created``, ...) are derived from the story JSON, and ``data`` holds the whole
story payload, so the reader/listening/quiz code keeps receiving exactly the
same objects it gets from the local ``stories/*.json`` files.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from env_loader import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_TABLE = "stories"
DEFAULT_TIMEOUT = 20.0
DEFAULT_BATCH_SIZE = 20
SUMMARY_COLUMNS = "id,title,level,created,seed_noun,bank,finished,n_han,n_questions"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY_VARS = ("SUPABASE_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
_PLACEHOLDER_URL = "your-project-ref"
_PLACEHOLDER_PREFIXES = ("your-", "sb_secret_your", "sb_publishable_your")


class SupabaseError(RuntimeError):
    """Raised when Supabase is misconfigured or a REST request fails."""


def is_placeholder(value: str | None) -> bool:
    """True for empty values and the placeholders shipped in ``.env.example``."""
    text = (value or "").strip().lower()
    return not text or text.startswith(_PLACEHOLDER_PREFIXES)


load_dotenv()


def _url_value() -> str:
    value = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if is_placeholder(value) or _PLACEHOLDER_URL in value.lower():
        return ""
    return value


def url() -> str:
    """Return the project REST URL, e.g. ``https://abcdefgh.supabase.co``."""
    value = _url_value()
    if not value:
        raise SupabaseError(
            "SUPABASE_URL is not set. Add it to .env "
            "(https://<project-ref>.supabase.co)."
        )
    return value


def api_key() -> str:
    """Return the server-side key (secret key or legacy service_role key)."""
    for name in _KEY_VARS:
        value = os.environ.get(name, "").strip()
        if value and not is_placeholder(value):
            return value
    raise SupabaseError(
        "SUPABASE_KEY is not set. Add the project's secret key to .env "
        "(never ship it to the browser)."
    )


def table() -> str:
    value = os.environ.get("SUPABASE_STORIES_TABLE", "").strip() or DEFAULT_TABLE
    if not _TABLE_RE.match(value):
        raise SupabaseError(f"invalid SUPABASE_STORIES_TABLE: {value!r}")
    return value


def configured() -> bool:
    """True when both a real project URL and a server-side key are available."""
    if not _url_value():
        return False
    return any(
        value and not is_placeholder(value)
        for value in (os.environ.get(name, "").strip() for name in _KEY_VARS)
    )


def _timeout() -> float:
    raw = os.environ.get("SUPABASE_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT


_client: httpx.Client | None = None


def client() -> httpx.Client:
    """Lazily build (and reuse) the HTTP client."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=_timeout())
    return _client


def close() -> None:
    """Close the shared client (used by scripts and tests)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def _endpoint() -> str:
    return f"{url()}/rest/v1/{table()}"


def _headers() -> dict[str, str]:
    key = api_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    *,
    params: dict[str, str] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    http: httpx.Client | None = None,
) -> httpx.Response:
    sender = http or client()
    try:
        response = sender.request(
            method,
            _endpoint(),
            params=params,
            json=json_body,
            headers={**_headers(), **(headers or {})},
        )
    except httpx.HTTPError as exc:
        raise SupabaseError(f"Supabase request failed: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text.strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        raise SupabaseError(
            f"Supabase {response.status_code}: {detail or response.reason_phrase}"
        )
    return response


def _check_id(story_id: str) -> str:
    value = str(story_id or "").strip()
    if not _ID_RE.match(value):
        raise SupabaseError(f"invalid story id: {value!r}")
    return value


def _level_text(level: Any) -> str | None:
    if level is None or isinstance(level, bool):
        return None
    if isinstance(level, int):
        return str(level)
    text = str(level).strip()
    return text or None


def _level_value(text: Any) -> int | str | None:
    if text is None or text == "":
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return str(text)


def _created_value(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def story_row(data: dict[str, Any], story_id: str | None = None) -> dict[str, Any]:
    """Flatten a story object into one ``stories`` table row."""
    if not isinstance(data, dict):
        raise SupabaseError("story is not a JSON object")
    ident = _check_id(str(story_id or data.get("id") or ""))
    payload = dict(data)
    payload["id"] = ident
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    questions = payload.get("questions")
    seed_noun = payload.get("seed_noun")
    return {
        "id": ident,
        "title": str(payload.get("title") or "无题"),
        "level": _level_text(payload.get("level")),
        "created": _created_value(payload.get("created")),
        "seed_noun": None if seed_noun is None else str(seed_noun),
        "bank": bool(payload.get("bank")),
        "finished": bool(payload.get("finished", False)),
        "n_han": _int_or_none(metrics.get("n_han")),
        "n_questions": len(questions) if isinstance(questions, list) else 0,
        "data": payload,
    }


def summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a summary row back into the shape ``GET /api/stories`` returns."""
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or "无题"),
        "level": _level_value(row.get("level")),
        "created": str(row.get("created") or ""),
        "seed_noun": row.get("seed_noun"),
        "bank": bool(row.get("bank")),
        "finished": bool(row.get("finished", False)),
        "n_han": row.get("n_han"),
        "n_questions": _int_or_none(row.get("n_questions")) or 0,
    }


def list_stories(*, http: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Return library summaries, newest first."""
    response = _request(
        "GET",
        params={"select": SUMMARY_COLUMNS, "order": "created.desc.nullslast"},
        http=http,
    )
    rows = response.json()
    if not isinstance(rows, list):
        raise SupabaseError("unexpected list response from Supabase")
    return [summary_from_row(row) for row in rows if isinstance(row, dict)]


def get_story(
    story_id: str, *, http: httpx.Client | None = None
) -> dict[str, Any] | None:
    """Return the stored story payload, or ``None`` when the row is missing."""
    ident = _check_id(story_id)
    response = _request(
        "GET",
        params={"select": "data", "id": f"eq.{ident}", "limit": "1"},
        http=http,
    )
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    data = row.get("data") if isinstance(row, dict) else None
    return data if isinstance(data, dict) else None


def _upsert_rows(rows: list[dict[str, Any]], *, http: httpx.Client | None = None) -> None:
    _request(
        "POST",
        params={"on_conflict": "id"},
        json_body=rows,
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        http=http,
    )


def save_stories(
    stories: Iterable[dict[str, Any]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    http: httpx.Client | None = None,
) -> int:
    """Upsert full story objects by ``id``; returns the number of stories sent."""
    size = max(1, int(batch_size))
    batch: list[dict[str, Any]] = []
    total = 0
    for story in stories:
        batch.append(story_row(story))
        if len(batch) >= size:
            _upsert_rows(batch, http=http)
            total += len(batch)
            batch = []
    if batch:
        _upsert_rows(batch, http=http)
        total += len(batch)
    return total


def save_story(story: dict[str, Any], *, http: httpx.Client | None = None) -> None:
    """Upsert one full story object."""
    save_stories([story], batch_size=1, http=http)


def fetch_stories(
    *, page_size: int = 50, http: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Return every stored story payload (paginated by id)."""
    size = max(1, int(page_size))
    stories: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = _request(
            "GET",
            params={
                "select": "data",
                "order": "id.asc",
                "limit": str(size),
                "offset": str(offset),
            },
            http=http,
        )
        rows = response.json()
        if not isinstance(rows, list):
            raise SupabaseError("unexpected fetch response from Supabase")
        for row in rows:
            payload = row.get("data") if isinstance(row, dict) else None
            if isinstance(payload, dict):
                stories.append(payload)
        if len(rows) < size:
            break
        offset += size
    return stories


def story_ids(*, page_size: int = 1000, http: httpx.Client | None = None) -> set[str]:
    """Return every stored story id (used by ``sync_supabase.py status``)."""
    size = max(1, int(page_size))
    found: set[str] = set()
    offset = 0
    while True:
        response = _request(
            "GET",
            params={
                "select": "id",
                "order": "id.asc",
                "limit": str(size),
                "offset": str(offset),
            },
            http=http,
        )
        rows = response.json()
        if not isinstance(rows, list):
            raise SupabaseError("unexpected id response from Supabase")
        for row in rows:
            ident = row.get("id") if isinstance(row, dict) else None
            if ident:
                found.add(str(ident))
        if len(rows) < size:
            break
        offset += size
    return found

