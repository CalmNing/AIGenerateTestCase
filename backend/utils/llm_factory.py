"""Unified construction of ChatOpenAI models and shared priority normalization.

Replaces 7+ duplicated ChatOpenAI() blocks across the pipeline agents and
model_utils. The `thinking: disabled` flag is written uniformly via
top-level `extra_body` (langchain_openai forwards it to the request body),
replacing the inconsistent mix of `extra_body=...` and
`model_kwargs={"extra_body": ...}` that previously existed.
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_THINKING_DISABLED = {"thinking": {"type": "disabled"}}

_PRIORITY_MAP = {
    "P0": "high", "HIGH": "high", "H": "high",
    "P1": "medium", "MEDIUM": "medium", "M": "medium",
    "P2": "low", "LOW": "low", "L": "low",
}


def build_chat_model(
    api_key: str,
    api_base_url: Optional[str] = None,
    api_model: Optional[str] = None,
    api_proxy_url: Optional[str] = None,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0,
    max_retries: int = 2,
    timeout=None,
    **overrides,
) -> ChatOpenAI:
    """Construct a ChatOpenAI for an OpenAI-compatible API (DeepSeek default).

    `max_tokens=None` lets the server apply its default; pass an explicit
    value (e.g. 16384) when long output must not be truncated.
    Extra keyword args are forwarded to ChatOpenAI unchanged.
    """
    extra_kwargs = dict(overrides)
    if api_proxy_url:
        import httpx
        extra_kwargs.setdefault(
            "http_async_client",
            httpx.AsyncClient(
                proxy=api_proxy_url,
                timeout=httpx.Timeout(None, connect=30.0, read=None, write=None, pool=None),
            ),
        )

    return ChatOpenAI(
        model=(api_model or "deepseek-v4-flash"),
        temperature=temperature,
        api_key=SecretStr(api_key),
        base_url=(api_base_url or _DEFAULT_BASE_URL),
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        extra_body=dict(_THINKING_DISABLED),
        **extra_kwargs,
    )


def normalize_priority(priority: Optional[str]) -> str:
    """Normalize a priority token to one of high/medium/low (default medium)."""
    if not priority:
        return "medium"
    return _PRIORITY_MAP.get(priority.upper(), "medium")
