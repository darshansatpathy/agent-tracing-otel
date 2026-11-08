"""Traced calls to any OpenAI-compatible chat completions endpoint (vLLM, LiteLLM, gateways, hosted APIs).

Usage, the served model, finish reasons and cached prompt tokens are read from the response, so the
span's cost is what the provider will bill.
"""

from __future__ import annotations

from typing import Any

import httpx

from agenttrace.tracing import llm


def chat(
    client: httpx.Client, model: str, messages: list[dict[str, Any]], *, provider: str = "openai-compatible", **params: Any
) -> dict[str, Any]:
    with llm(model, provider=provider, temperature=params.get("temperature"), max_tokens=params.get("max_tokens")) as call:
        r = client.post("/chat/completions", json={"model": model, "messages": messages, **params})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        call.usage(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0), cache_read_tokens=cached or 0)
        call.response(
            model=data.get("model"),
            finish_reasons=[c.get("finish_reason") for c in data.get("choices", []) if c.get("finish_reason")],
            response_id=data.get("id"),
        )
        call.content(prompt=str(messages), completion=(data.get("choices") or [{}])[0].get("message", {}).get("content"))
        return data
