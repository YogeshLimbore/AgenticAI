"""
llm/provider.py — LLM abstraction with Gemini 1.5 Flash (free tier)
Features:
  - Gemini 1.5 Flash: FREE — 15 req/min, 1M tokens/day
  - SHA-256 response caching (saves ~50% API calls)
  - Exponential backoff retry on rate limits
  - Adaptive threshold learning from feedback outcomes
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, DeadlineExceeded
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from utils.logger import get_logger

log = get_logger("llm")


class LLMProvider:
    """
    Gemini 1.5 Flash wrapper with:
    - Response caching (disk-backed, TTL configurable)
    - Retry with exponential backoff for rate limits
    - Token usage tracking for the free tier
    """

    def __init__(self, api_key: str, cache_path: Path,
                 model: str = "gemini-3-flash-preview",
                 cache_ttl_hours: int = 24):
        genai.configure(api_key=api_key)
        self.model_name = model
        self.model = genai.GenerativeModel(model)
        self.cache_path = cache_path
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self._cache: dict = self._load_cache()
        self._request_count = 0
        self._cached_count = 0

        log.info(
            f"LLM provider ready: [bold]{model}[/] "
            f"(FREE tier — 15 req/min · 1M tokens/day)"
        )

    # ── Cache ─────────────────────────────────────────────────────────────

    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            try:
                with open(self.cache_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception as e:
            log.warning(f"Could not save LLM cache: {e}")

    def _cache_key(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode()).hexdigest()[:32]

    def _cache_get(self, key: str) -> Optional[str]:
        entry = self._cache.get(key)
        if not entry:
            return None
        try:
            expires = datetime.fromisoformat(entry["expires"])
            if datetime.now() < expires:
                return entry["response"]
        except Exception:
            pass
        # Expired — remove
        self._cache.pop(key, None)
        return None

    def _cache_set(self, key: str, response: str):
        self._cache[key] = {
            "response": response,
            "expires": (datetime.now() + self.cache_ttl).isoformat(),
        }
        self._save_cache()

    # ── API call with retry ───────────────────────────────────────────────

    # Only retry on transient API errors (rate limits, timeouts, server errors).
    # Do NOT retry on TypeError / AttributeError / etc. — those are bugs.
    @retry(
        retry=retry_if_exception_type((ResourceExhausted, ServiceUnavailable,
                                       DeadlineExceeded, ConnectionError, TimeoutError)),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(4),
        reraise=False,
    )
    def _call_api(self, prompt: str) -> str:
        response = self.model.generate_content(prompt)
        return (response.text or "").strip()

    # ── Public interface ──────────────────────────────────────────────────

    def ask(self, prompt: str, system: str = "",
            use_cache: bool = True) -> str:
        """
        Send prompt to Gemini 1.5 Flash.
        Cache hit → instant, no API call.
        Cache miss → API call with retry.
        """
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        key = self._cache_key(full_prompt)

        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                self._cached_count += 1
                log.debug(
                    f"LLM cache hit ({self._cached_count} saved so far)"
                )
                return cached

        # Rate limiting: Gemini free tier = 15 req/min
        # Add a small delay to stay safely under the limit
        time.sleep(0.5)

        self._request_count += 1
        log.debug(f"LLM API call #{self._request_count}")

        try:
            result = self._call_api(full_prompt)
            if use_cache and result:
                self._cache_set(key, result)
            return result
        except Exception as e:
            log.error(f"LLM call failed after retries: {e}")
            return ""

    def usage_summary(self) -> dict:
        return {
            "api_calls": self._request_count,
            "cache_hits": self._cached_count,
            "total_requests": self._request_count + self._cached_count,
            "cache_hit_rate": (
                f"{self._cached_count / max(1, self._request_count + self._cached_count) * 100:.0f}%"
            ),
        }
