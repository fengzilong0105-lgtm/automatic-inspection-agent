from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from agent.settings import get_settings


def normalize_ollama_base_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned[:-3]
    return cleaned


def create_chat_model(
    *,
    provider: str,
    model: str,
    base_url: str = "",
    api_key: str = "",
    ollama_base_url: str = "http://localhost:11434",
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> BaseChatModel:
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            base_url=normalize_ollama_base_url(ollama_base_url),
            temperature=temperature,
            num_predict=max_tokens,
        )

    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return ChatOpenAI(**kwargs)


def get_llm(task: str = "chat_qa") -> BaseChatModel:
    settings = get_settings().config.llm
    default = settings.default
    route = settings.routing.get(task)
    model = route.model if route and route.model else default.model
    temperature = route.temperature if route and route.temperature is not None else default.temperature
    max_tokens = route.max_tokens if route and route.max_tokens else default.max_tokens

    return create_chat_model(
        provider=default.provider,
        model=model,
        base_url=default.base_url,
        api_key=default.api_key,
        ollama_base_url=settings.ollama_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )
