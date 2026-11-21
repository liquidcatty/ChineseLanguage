"""DeepSeek chat helper (OpenAI-compatible JSON mode).

The API key is read from ``DEEPSEEK_API_KEY`` (environment or ``.env``); the
model defaults to ``deepseek-flash`` (DeepSeek-V4.1-Flash) and can be overridden
with ``DEEPSEEK_MODEL``.  Nothing secret is ever stored in this file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable

from openai import OpenAI

from env_loader import load_dotenv

load_dotenv()

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"  # DeepSeek-V4.1-Flash-1017
DEFAULT_MAX_TOKENS = 8192
API_RETRIES = 3
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
PLACEHOLDER_KEYS = {
    "sk-your-deepseek-key-here",
    "sk-replace-me",
    "your-deepseek-api-key",
}

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when the model cannot be reached or keeps returning bad JSON."""


_usage_lock = threading.Lock()
_usage: dict[str, int] = {
    "calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}

_client: OpenAI | None = None


def api_key() -> str:
    """Return ``DEEPSEEK_API_KEY`` or raise a helpful error.

    Placeholder values from ``.env.example`` count as missing, so a copied
    template fails with a clear message instead of an HTTP 401.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key or key.lower() in PLACEHOLDER_KEYS:
        raise LLMError(
            "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and replace "
            "the placeholder (or export the real key); it is never read from the "
            "repository."
        )
    return key


def model_name() -> str:
    """Model id sent to the API; ``deepseek-flash`` unless overridden."""
    return os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL


def get_client() -> OpenAI:
    """Lazily build (and reuse) the OpenAI-compatible DeepSeek client."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=api_key(), base_url=BASE_URL)
    return _client


def reset_usage() -> None:
    with _usage_lock:
        for key in _usage:
            _usage[key] = 0


def usage_snapshot() -> dict[str, int]:
    with _usage_lock:
        return dict(_usage)


def _record_usage(usage: Any) -> None:
    if usage is None:
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0) or prompt + completion
    with _usage_lock:
        _usage["calls"] += 1
        _usage["prompt_tokens"] += prompt
        _usage["completion_tokens"] += completion
        _usage["total_tokens"] += total
    logger.info(
        "deepseek usage: prompt=%d completion=%d total=%d",
        prompt,
        completion,
        total,
    )


def _parse_json(content: str) -> dict[str, Any]:
    if not content or not content.strip():
        raise ValueError("empty response body")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return data


def chat_json(
    system: str,
    user: str,
    temperature: float = 1.0,
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Ask the model for a JSON object, retrying transient failures.

    Up to three API failures are retried with exponential backoff and one extra
    attempt is made when the reply is not valid JSON.  Token usage is logged and
    accumulated for the run report.
    """
    active_client = client if client is not None else get_client()
    api_errors = 0
    json_retry_used = False

    while True:
        try:
            response = active_client.chat.completions.create(
                model=model or model_name(),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                # Temperature only matters when thinking is off; the tiered
                # pipeline controls temperature per call, so disable it.
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as exc:  # network, auth, rate limit, ...
            if api_errors < API_RETRIES:
                delay = BACKOFF_SECONDS[min(api_errors, len(BACKOFF_SECONDS) - 1)]
                api_errors += 1
                logger.warning(
                    "deepseek request failed (%s: %s); retry %d/%d in %.1fs",
                    type(exc).__name__,
                    exc,
                    api_errors,
                    API_RETRIES,
                    delay,
                )
                sleep(delay)
                continue
            raise LLMError(
                f"deepseek request failed after {API_RETRIES} retries: {exc}"
            ) from exc

        _record_usage(getattr(response, "usage", None))
        choices = getattr(response, "choices", None) or []
        if not choices:
            if api_errors < API_RETRIES:
                delay = BACKOFF_SECONDS[min(api_errors, len(BACKOFF_SECONDS) - 1)]
                api_errors += 1
                logger.warning("deepseek returned no choices; retrying in %.1fs", delay)
                sleep(delay)
                continue
            raise LLMError("deepseek returned no choices")

        content = choices[0].message.content or ""
        try:
            return _parse_json(content)
        except (json.JSONDecodeError, ValueError) as exc:
            if json_retry_used:
                raise LLMError(
                    "deepseek did not return valid JSON after one retry: "
                    f"{content[:300]!r}"
                ) from exc
            json_retry_used = True
            logger.warning("invalid JSON from deepseek (%s); retrying once", exc)
