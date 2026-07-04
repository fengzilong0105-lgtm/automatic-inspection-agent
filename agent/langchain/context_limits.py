from __future__ import annotations

import logging
from typing import Any

import httpx

from agent.langchain.llm_factory import normalize_ollama_base_url
from agent.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_OPENAI_CONTEXT_LIMITS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 128_000,
    "gpt-3.5-turbo": 16_385,
}

_DEFAULT_CONTEXT_LIMIT = 128_000
_OLLAMA_DEFAULT_CONTEXT_LIMIT = 32_768


def _parse_ollama_context_length(payload: dict[str, Any], model: str) -> int | None:
    model_info = payload.get("model_info") or {}
    for key, value in model_info.items():
        if key.endswith("context_length"):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    details = payload.get("details") or {}
    family = details.get("family") or model.split(":")[0]
    for key, value in model_info.items():
        if family in key and "context" in key:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


async def fetch_ollama_context_limit(model: str, base_url: str) -> int | None:
    url = f"{normalize_ollama_base_url(base_url)}/api/show"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, json={"name": model})
            response.raise_for_status()
            return _parse_ollama_context_length(response.json(), model)
    except Exception as exc:
        logger.warning("Failed to fetch Ollama context limit for %s: %s", model, exc)
        return None


def lookup_openai_context_limit(model: str) -> int:
    normalized = (model or "").strip().lower()
    if normalized in _OPENAI_CONTEXT_LIMITS:
        return _OPENAI_CONTEXT_LIMITS[normalized]
    for key, value in _OPENAI_CONTEXT_LIMITS.items():
        if normalized.startswith(key):
            return value
    return _DEFAULT_CONTEXT_LIMIT


async def resolve_context_limit(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    llm = settings.config.llm.default
    if llm.provider == "ollama":
        limit = await fetch_ollama_context_limit(llm.model, settings.config.llm.ollama_base_url)
        return limit or _OLLAMA_DEFAULT_CONTEXT_LIMIT
    return lookup_openai_context_limit(llm.model)
