"""Unified construction of ChatOpenAI models and shared priority normalization.

Replaces 7+ duplicated ChatOpenAI() blocks across the pipeline agents and
model_utils. The `thinking: disabled` flag is written uniformly via
top-level `extra_body` (langchain_openai forwards it to the request body),
replacing the inconsistent mix of `extra_body=...` and
`model_kwargs={"extra_body": ...}` that previously existed.
"""
from typing import Optional
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_THINKING_DISABLED = {"thinking": {"type": "disabled"}}
_THINKING_ENABLED = {"thinking": {"type": "enabled"}}


def _is_deepseek_host(base_url: Optional[str]) -> bool:
    """Return True for DeepSeek (or DeepSeek-compatible) hosts.

    Used to auto-enable prompt caching only where the provider actually
    supports the ``enable_prompt_cache`` flag, avoiding 400s on gateways
    that reject unknown extra_body fields.
    """
    host = (urlparse(base_url or _DEFAULT_BASE_URL).hostname or "").lower()
    return "deepseek" in host

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
    prompt_cache: Optional[bool] = None,
    thinking: Optional[bool] = None,
    **overrides,
) -> ChatOpenAI:
    """Construct a ChatOpenAI for an OpenAI-compatible API (DeepSeek default).

    `max_tokens=None` lets the server apply its default; pass an explicit
    value (e.g. 16384) when long output must not be truncated.
    Extra keyword args are forwarded to ChatOpenAI unchanged.

    `thinking` controls DeepSeek thinking mode (reasoning_content):
      - None (default): disabled — structured-output and generation flows rely
        on direct answers; enabling it here would break parsing.
      - True / False: force enabled / disabled (chat streaming uses True to
        surface the reasoning process in the UI).

    `prompt_cache` controls LLM prompt caching (prefix reuse):
      - None (default): auto-enable for DeepSeek-compatible hosts, off elsewhere.
      - True / False: force enable / disable (used by the config toggle so a
        user on a compatible gateway can turn it on even if the host name
        doesn't contain "deepseek").
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

    # Prompt caching: cache the (large, static) system-prefix so repeated
    # generation requests don't re-process the same instructions every time.
    enable_cache = _is_deepseek_host(api_base_url) if prompt_cache is None else prompt_cache
    extra_body = dict(_THINKING_DISABLED if not thinking else _THINKING_ENABLED)
    if enable_cache:
        extra_body["enable_prompt_cache"] = True

    return DeepSeekCompatChatOpenAI(
        model=(api_model or "deepseek-v4-flash"),
        temperature=temperature,
        api_key=SecretStr(api_key),
        base_url=(api_base_url or _DEFAULT_BASE_URL),
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        extra_body=extra_body,
        **extra_kwargs,
    )


def normalize_priority(priority: Optional[str]) -> str:
    """Normalize a priority token to one of high/medium/low (default medium)."""
    if not priority:
        return "medium"
    return _PRIORITY_MAP.get(priority.upper(), "medium")


class DeepSeekCompatChatOpenAI(ChatOpenAI):
    """保留 DeepSeek reasoning_content 的 ChatOpenAI。

    langchain_openai 的流式 delta 转换（_convert_delta_to_message_chunk）
    只保留 function_call/tool_calls，把 delta.reasoning_content 直接丢弃，
    导致 Agent 路径（astream_events → on_chat_model_stream）拿不到思考增量。
    这里在转换完成后把原始 delta 的 reasoning_content 补进 additional_kwargs，
    chat.py 的 _sse_chunk 已按该键读取。plain-http 直连路径不受影响。
    """

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is not None:
            try:
                choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
                if choices:
                    reasoning = (choices[0].get("delta") or {}).get("reasoning_content")
                    if reasoning:
                        msg = generation_chunk.message
                        msg.additional_kwargs = {**(msg.additional_kwargs or {}), "reasoning_content": reasoning}
            except Exception:  # noqa: BLE001 - 补丁失败不影响原转换
                pass
        return generation_chunk