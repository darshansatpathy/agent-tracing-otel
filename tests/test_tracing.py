from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from conftest import ROOT
from opentelemetry.trace import StatusCode

import agenttrace
from agenttrace import llm, step, task, tool
from agenttrace.pricing import Usage


def test_task_llm_tool_spans_follow_genai_conventions(otel):
    with task("support-triage", task_id="T-1", conversation_id="c-1", user_id="u-9") as t:
        with step("draft"), llm("large-model", temperature=0.1, max_tokens=800) as call:
            call.usage(input_tokens=5000, output_tokens=400, cache_read_tokens=3000)
            call.response(model="large-model", finish_reasons=["stop"], response_id="r-1")
        with tool("search_kb", call_id="call-1"):
            pass
        t.outcome("success")
    root = otel.by_name("invoke_agent")[0]
    chat = otel.by_name("chat large-model")[0]
    tl = otel.by_name("execute_tool search_kb")[0]
    a = chat.attributes
    assert (
        a["gen_ai.operation.name"] == "chat"
        and a["gen_ai.request.model"] == "large-model"
        and a["gen_ai.provider.name"] == "openai-compatible"
    )
    assert (
        a["gen_ai.usage.input_tokens"] == 5000
        and a["gen_ai.usage.output_tokens"] == 400
        and a["gen_ai.usage.cache_read.input_tokens"] == 3000
    )
    assert a["gen_ai.response.finish_reasons"] == ("stop",) and a["gen_ai.request.temperature"] == 0.1
    expected = agenttrace.tracing._config.prices.cost("large-model", Usage(5000, 400, 3000), dt.date.today())
    assert a["agent.cost_usd"] == pytest.approx(expected)
    assert tl.attributes["gen_ai.tool.name"] == "search_kb" and tl.attributes["gen_ai.tool.call.id"] == "call-1"
    r = root.attributes
    assert r["gen_ai.operation.name"] == "invoke_agent" and r["gen_ai.agent.name"] == "support-triage" and r["agent.task.id"] == "T-1"
    assert r["gen_ai.conversation.id"] == "c-1" and r["enduser.id"] == "u-9"
    assert r["agent.task.outcome"] == "success" and r["agent.task.cost_usd"] == pytest.approx(expected)
    assert r["agent.task.llm_calls"] == 1 and r["agent.task.tool_calls"] == 1 and r["agent.task.input_tokens"] == 5000
    assert chat.parent.span_id == otel.by_name("draft")[0].context.span_id
    assert otel.by_name("draft")[0].parent.span_id == root.context.span_id


def test_an_exception_fails_the_task_and_keeps_partial_cost(otel):
    with pytest.raises(TimeoutError), task("t") as t:
        with llm("small-model") as call:
            call.usage(1000, 10)
        with llm("large-model"):
            raise TimeoutError("model did not answer")
    root = otel.by_name("invoke_agent")[0]
    assert root.attributes["agent.task.outcome"] == "failure" and root.status.status_code == StatusCode.ERROR
    assert root.attributes["error.type"] == "TimeoutError" and root.attributes["agent.task.llm_calls"] == 2
    failed = otel.by_name("chat large-model")[0]
    assert failed.status.status_code == StatusCode.ERROR and failed.attributes["error.type"] == "TimeoutError"
    assert root.attributes["agent.task.cost_usd"] > 0
    assert t.outcome  # the handle stays usable


def test_explicit_outcomes_and_reasons(otel):
    with task("t") as t:
        t.outcome("escalated", "needs a human")
    a = otel.by_name("invoke_agent")[0].attributes
    assert a["agent.task.outcome"] == "escalated" and a["agent.task.outcome_reason"] == "needs a human"


def test_unpriced_models_are_counted_not_treated_as_free(otel):
    with task("t"), llm("mystery-model") as call:
        call.usage(100, 100)
    root = otel.by_name("invoke_agent")[0]
    assert root.attributes["agent.task.unpriced_calls"] == 1 and root.attributes["agent.task.cost_usd"] == 0
    assert "agent.cost_usd" not in otel.by_name("chat mystery-model")[0].attributes
    assert sum(p.value for p in otel.metric_points("gen_ai.client.unpriced_tokens")) >= 200


def test_concurrent_tasks_keep_separate_totals(otel):
    async def one(i: int) -> None:
        with task("parallel", task_id=str(i)):

            async def call(n: int) -> None:
                await asyncio.sleep(0.001 * (n % 3))
                with llm("small-model") as c:
                    c.usage(1000 * (i + 1), 0)

            await asyncio.gather(*(call(n) for n in range(3)))

    async def main() -> None:
        await asyncio.gather(*(one(i) for i in range(4)))

    asyncio.run(main())
    roots = {s.attributes["agent.task.id"]: s.attributes for s in otel.by_name("invoke_agent")}
    for i in range(4):
        assert roots[str(i)]["agent.task.input_tokens"] == 3000 * (i + 1) and roots[str(i)]["agent.task.llm_calls"] == 3


def test_content_is_not_captured_unless_enabled(otel):
    with llm("small-model") as call:
        call.content("my email is ana@example.com", "ok")
    assert "gen_ai.input.messages" not in otel.spans()[-1].attributes
    agenttrace.configure(
        prices=ROOT / "prices.yaml", reset_instruments=True, capture_content=True, redact=lambda s: s.replace("ana@example.com", "[EMAIL]")
    )
    with llm("small-model") as call:
        call.content("my email is ana@example.com", "ok")
    a = otel.spans()[-1].attributes
    assert a["gen_ai.input.messages"] == "my email is [EMAIL]" and a["gen_ai.output.messages"] == "ok"


def test_metrics(otel):
    with task("m") as t:
        with llm("small-model") as c:
            c.usage(1200, 30)
        with tool("x"):
            pass
        t.outcome("success")
    tokens = {
        (p.attributes["gen_ai.token.type"], p.attributes["gen_ai.agent.name"]): p.sum
        for p in otel.metric_points("gen_ai.client.token.usage")
        if p.attributes.get("gen_ai.agent.name") == "m"
    }
    assert tokens == {("input", "m"): 1200, ("output", "m"): 30}
    tasks = [p for p in otel.metric_points("agent.tasks") if p.attributes["gen_ai.agent.name"] == "m"]
    assert tasks[0].value == 1 and tasks[0].attributes["agent.task.outcome"] == "success"
    assert any(p.attributes.get("gen_ai.agent.name") == "m" for p in otel.metric_points("agent.task.cost"))
    assert any(p.attributes.get("gen_ai.tool.name") == "x" for p in otel.metric_points("agent.tool.duration"))
    assert any(p.attributes.get("gen_ai.request.model") == "small-model" for p in otel.metric_points("gen_ai.client.operation.duration"))


def test_calls_outside_a_task_still_trace(otel):
    with llm("small-model") as c:
        c.usage(10, 1)
    with tool("lonely"):
        pass
    assert {s.name for s in otel.spans()} == {"chat small-model", "execute_tool lonely"}


def test_failed_step_and_tool_mark_errors(otel):
    with pytest.raises(ValueError), step("plan"):
        raise ValueError("bad plan")
    with pytest.raises(KeyError), tool("crm"):
        raise KeyError("missing")
    assert all(s.status.status_code == StatusCode.ERROR for s in otel.spans())
