from __future__ import annotations

import httpx
import pytest

from agenttrace import task
from agenttrace.integrations.openai_compat import chat


def test_chat_records_usage_cache_and_cost(otel):
    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "large-model",
                "choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 1500}},
            },
        )

    client = httpx.Client(base_url="http://llm.test/v1", transport=httpx.MockTransport(handle))
    with task("t"):
        out = chat(client, "large-model", [{"role": "user", "content": "hi"}], temperature=0)
    assert out["choices"][0]["message"]["content"] == "Done."
    span = otel.by_name("chat large-model")[0]
    a = span.attributes
    assert a["gen_ai.usage.input_tokens"] == 2000 and a["gen_ai.usage.cache_read.input_tokens"] == 1500
    assert a["gen_ai.response.id"] == "chatcmpl-1" and a["gen_ai.response.finish_reasons"] == ("stop",)
    assert a["agent.cost_usd"] > 0 and "gen_ai.input.messages" not in a


def test_http_errors_mark_the_span(otel):
    client = httpx.Client(base_url="http://llm.test/v1", transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(httpx.HTTPStatusError):
        chat(client, "small-model", [])
    assert otel.by_name("chat small-model")[0].attributes["error.type"] == "HTTPStatusError"
