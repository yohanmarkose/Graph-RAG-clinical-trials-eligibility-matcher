from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract base class for LLM completion providers."""

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> str:
        """Return completion text for the given system + user prompt pair."""


# ---------------------------------------------------------------------------
# OpenAI implementation
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    """OpenAI chat completions (async) provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        from openai import AsyncOpenAI  # type: ignore[import-untyped]

        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> str:
        from openai import RateLimitError  # type: ignore[import-untyped]

        # 3 retries with exponential backoff: 1s, 2s, 4s
        delays = [1, 2, 4]
        last_exc: Exception | None = None

        for attempt, delay in enumerate(delays + [None], start=1):  # type: ignore[assignment]
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except RateLimitError as exc:
                last_exc = exc
                if delay is None:
                    break
                logger.warning(
                    "OpenAI rate limit hit (attempt %d/%d); retrying in %ds",
                    attempt,
                    len(delays) + 1,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                last_exc = exc
                if delay is None:
                    break
                logger.warning(
                    "OpenAI request error (attempt %d/%d): %s; retrying in %ds",
                    attempt,
                    len(delays) + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError(
            f"OpenAI request failed after {len(delays) + 1} attempts"
        ) from last_exc


# ---------------------------------------------------------------------------
# Anthropic implementation
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    """Anthropic messages (async) provider."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        from anthropic import AsyncAnthropic  # type: ignore[import-untyped]

        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> str:
        from anthropic import RateLimitError  # type: ignore[import-untyped]

        delays = [1, 2, 4]
        last_exc: Exception | None = None

        for attempt, delay in enumerate(delays + [None], start=1):  # type: ignore[assignment]
            try:
                response = await self._client.messages.create(
                    model=self.model,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.content[0].text
            except RateLimitError as exc:
                last_exc = exc
                if delay is None:
                    break
                logger.warning(
                    "Anthropic rate limit hit (attempt %d/%d); retrying in %ds",
                    attempt,
                    len(delays) + 1,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                last_exc = exc
                if delay is None:
                    break
                logger.warning(
                    "Anthropic request error (attempt %d/%d): %s; retrying in %ds",
                    attempt,
                    len(delays) + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError(
            f"Anthropic request failed after {len(delays) + 1} attempts"
        ) from last_exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_llm_provider(settings: Any) -> LLMProvider:
    """Return the configured LLM provider from application settings.

    Reads settings.llm.provider to select between "openai" (default)
    and "anthropic".
    """
    llm_cfg = settings.llm
    provider = llm_cfg.provider.lower()

    if provider == "anthropic":
        return AnthropicProvider(
            api_key=llm_cfg.anthropic_api_key,
            model=llm_cfg.model,
        )

    # Default to OpenAI
    return OpenAIProvider(
        api_key=llm_cfg.openai_api_key,
        model=llm_cfg.model,
    )


# ---------------------------------------------------------------------------
# Caching wrapper
# ---------------------------------------------------------------------------


class CachedLLMProvider(LLMProvider):
    """Wraps another LLMProvider with an in-memory response cache.

    Cache key: SHA-256 of (system_prompt + NUL + user_prompt).
    Useful during development to avoid re-calling the LLM on identical criteria.

    Example::

        inner = get_llm_provider(settings)
        llm   = CachedLLMProvider(inner)
        llm.load_cache("data/processed/llm_cache.json")  # warm from disk
        ...
        llm.save_cache("data/processed/llm_cache.json")  # persist for next run
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self._cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(system_prompt: str, user_prompt: str) -> str:
        payload = f"{system_prompt}\x00{user_prompt}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> str:
        key = self._cache_key(system_prompt, user_prompt)
        if key in self._cache:
            logger.debug("LLM cache hit (key prefix: %s…)", key[:12])
            return self._cache[key]

        result = await self._inner.complete(system_prompt, user_prompt, temperature, max_tokens)
        self._cache[key] = result
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_cache(self, path: str | Path) -> None:
        """Persist the in-memory cache to a JSON file."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        logger.info("LLM cache saved: %d entries → %s", len(self._cache), out)

    def load_cache(self, path: str | Path) -> None:
        """Load a previously persisted cache from a JSON file."""
        p = Path(path)
        if not p.exists():
            logger.info("No LLM cache file at %s — starting fresh", p)
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        self._cache.update(data)
        logger.info("LLM cache loaded: %d entries from %s", len(data), p)

    @property
    def cache_size(self) -> int:
        return len(self._cache)
