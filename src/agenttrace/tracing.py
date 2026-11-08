"""Spans and metrics for agent runs, following the OpenTelemetry GenAI semantic conventions.

- `task(...)`: the root span of one unit of agent work (`invoke_agent`). Every LLM call and tool call
  inside it adds to its totals: cost, tokens, calls. When it ends it records its outcome, so the
  dashboards can divide spend by *completed* tasks rather than by calls.
- `llm(...)`: a model call (`chat`, `text_completion`, `embeddings`). Report usage from the provider's
  response; cost comes from the price table.
- `tool(...)`: a tool execution (`execute_tool`).
- `step(...)`: any other phase worth seeing (planning, retrieval, a retry loop).

Prompt and completion text is not recorded unless `capture_content=True`, and then only after the
redaction function runs.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from opentelemetry import metrics, trace
from opentelemetry.semconv._incubating.attributes import error_attributes as EA
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as GA
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agenttrace.pricing import PriceTable, Usage

Outcome = Literal["success", "failure", "escalated", "abandoned"]
SCOPE = "agenttrace"

# Attributes this library adds beyond the GenAI conventions, under one prefix.
COST = "agent.cost_usd"
TASK_ID = "agent.task.id"
TASK_OUTCOME = "agent.task.outcome"
TASK_COST = "agent.task.cost_usd"
TASK_INPUT = "agent.task.input_tokens"
TASK_OUTPUT = "agent.task.output_tokens"
TASK_LLM_CALLS = "agent.task.llm_calls"
TASK_TOOL_CALLS = "agent.task.tool_calls"
TASK_UNPRICED = "agent.task.unpriced_calls"
STEP_NAME = "agent.step.name"


@dataclass
class Config:
    prices: PriceTable | None = None
    capture_content: bool = False
    redact: Callable[[str], str] = field(default=lambda s: s)
    max_content_chars: int = 4000
    tracer_provider: trace.TracerProvider | None = None
    meter_provider: metrics.MeterProvider | None = None


_config = Config()


class _Instruments:
    def __init__(self) -> None:
        m = metrics.get_meter(SCOPE, meter_provider=_config.meter_provider)
        self.tokens = m.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Tokens used per call",
            explicit_bucket_boundaries_advisory=[1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304],
        )
        self.duration = m.create_histogram(
            "gen_ai.client.operation.duration",
            unit="s",
            description="Duration of a model call",
            explicit_bucket_boundaries_advisory=[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92],
        )
        self.call_cost = m.create_counter("gen_ai.client.cost", unit="usd", description="Spend on model calls")
        self.unpriced = m.create_counter("gen_ai.client.unpriced_tokens", unit="{token}", description="Tokens on models with no price")
        self.tasks = m.create_counter("agent.tasks", unit="{task}", description="Finished tasks by outcome")
        self.task_cost = m.create_histogram(
            "agent.task.cost",
            unit="usd",
            description="Spend per task",
            explicit_bucket_boundaries_advisory=[0.0001, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
        )
        self.task_duration = m.create_histogram(
            "agent.task.duration",
            unit="s",
            description="Duration of a task",
            explicit_bucket_boundaries_advisory=[0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80, 160, 320],
        )
        self.tool_duration = m.create_histogram("agent.tool.duration", unit="s", description="Duration of a tool call")


_inst: _Instruments | None = None


def _instruments() -> _Instruments:
    global _inst
    if _inst is None:
        _inst = _Instruments()
    return _inst


def configure(
    *,
    prices: PriceTable | Path | str | None = None,
    capture_content: bool = False,
    redact: Callable[[str], str] | None = None,
    tracer_provider: trace.TracerProvider | None = None,
    meter_provider: metrics.MeterProvider | None = None,
    reset_instruments: bool = False,
) -> None:
    """Set the price table and content capture. Spans and metrics go to the global OpenTelemetry providers
    unless providers are passed here."""
    global _config, _inst
    table = PriceTable.load(Path(prices)) if isinstance(prices, (str, Path)) else prices
    _config = Config(table, capture_content, redact or (lambda s: s), tracer_provider=tracer_provider, meter_provider=meter_provider)
    if reset_instruments or meter_provider is not None:
        _inst = None


def _tracer() -> trace.Tracer:
    return trace.get_tracer(SCOPE, tracer_provider=_config.tracer_provider)


@dataclass
class Task:
    name: str
    span: Span
    task_id: str | None
    started: int = field(default_factory=lambda: _clock())
    cost: float = 0.0
    usage: Usage = field(default_factory=Usage)
    llm_calls: int = 0
    tool_calls: int = 0
    unpriced_calls: int = 0
    _outcome: Outcome | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def outcome(self, value: Outcome, reason: str | None = None) -> None:
        self._outcome = value
        if reason:
            self.span.set_attribute("agent.task.outcome_reason", reason[:500])

    def _add_llm(self, cost: float | None, usage: Usage) -> None:
        with self._lock:
            self.llm_calls += 1
            self.usage = self.usage + usage
            if cost is None:
                self.unpriced_calls += 1
            else:
                self.cost += cost

    def _add_tool(self) -> None:
        with self._lock:
            self.tool_calls += 1


_clock: Callable[[], int] = time.time_ns


def set_clock(clock: Callable[[], int] | None) -> None:
    """Use a different clock for span times and durations (simulations, tests). None restores wall time."""
    global _clock
    _clock = clock or time.time_ns


@contextmanager
def _span(name: str, kind: SpanKind, attrs: dict[str, Any]) -> Iterator[Span]:
    span = _tracer().start_span(name, kind=kind, attributes=attrs, start_time=_clock())
    with trace.use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
        try:
            yield span
        finally:
            span.end(end_time=_clock())


_current: contextvars.ContextVar[Task | None] = contextvars.ContextVar("agenttrace_task", default=None)


def current_task() -> Task | None:
    return _current.get()


@contextmanager
def task(
    name: str,
    *,
    task_id: str | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Task]:
    """One unit of agent work. The outcome defaults to success, or failure if an exception escapes."""
    attrs: dict[str, Any] = {GA.GEN_AI_OPERATION_NAME: "invoke_agent", GA.GEN_AI_AGENT_NAME: name}
    if task_id:
        attrs[TASK_ID] = task_id
    if conversation_id:
        attrs[GA.GEN_AI_CONVERSATION_ID] = conversation_id
    if user_id:
        attrs["enduser.id"] = user_id
    attrs.update(attributes or {})
    with _span(f"invoke_agent {name}", SpanKind.INTERNAL, attrs) as span:
        t = Task(name, span, task_id)
        token = _current.set(t)
        try:
            yield t
        except BaseException as e:
            t._outcome = t._outcome if t._outcome not in (None, "success") else "failure"
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, f"{type(e).__name__}: {e}"[:300]))
            span.set_attribute(EA.ERROR_TYPE, type(e).__qualname__)
            raise
        finally:
            _current.reset(token)
            outcome = t._outcome or "success"
            elapsed = (_clock() - t.started) / 1e9
            span.set_attributes(
                {
                    TASK_OUTCOME: outcome,
                    TASK_COST: round(t.cost, 8),
                    TASK_INPUT: t.usage.input_tokens,
                    TASK_OUTPUT: t.usage.output_tokens,
                    TASK_LLM_CALLS: t.llm_calls,
                    TASK_TOOL_CALLS: t.tool_calls,
                    TASK_UNPRICED: t.unpriced_calls,
                }
            )
            if outcome == "failure" and span.is_recording():
                span.set_status(Status(StatusCode.ERROR, "task failed"))
            labels = {GA.GEN_AI_AGENT_NAME: name, TASK_OUTCOME: outcome}
            i = _instruments()
            i.tasks.add(1, labels)
            i.task_cost.record(t.cost, labels)
            i.task_duration.record(elapsed, labels)


class LLMCall:
    def __init__(self, span: Span, model: str, provider: str, operation: str):
        self.span, self.model, self.provider, self.operation = span, model, provider, operation
        self._usage = Usage()
        self.response_model: str | None = None
        self.cost: float | None = None
        self.error: str | None = None

    def usage(self, input_tokens: int = 0, output_tokens: int = 0, *, cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> None:
        self._usage = Usage(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)

    def response(self, *, model: str | None = None, finish_reasons: list[str] | None = None, response_id: str | None = None) -> None:
        if model:
            self.response_model = model
            self.span.set_attribute(GA.GEN_AI_RESPONSE_MODEL, model)
        if finish_reasons:
            self.span.set_attribute(GA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reasons)
        if response_id:
            self.span.set_attribute(GA.GEN_AI_RESPONSE_ID, response_id)

    def content(self, prompt: str | None = None, completion: str | None = None) -> None:
        """Recorded only when content capture is on, after redaction and truncation."""
        if not _config.capture_content:
            return
        cut = _config.max_content_chars
        if prompt is not None:
            self.span.set_attribute(GA.GEN_AI_INPUT_MESSAGES, _config.redact(prompt)[:cut])
        if completion is not None:
            self.span.set_attribute(GA.GEN_AI_OUTPUT_MESSAGES, _config.redact(completion)[:cut])


@contextmanager
def llm(
    model: str,
    *,
    provider: str = "openai-compatible",
    operation: str = "chat",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Iterator[LLMCall]:
    attrs: dict[str, Any] = {GA.GEN_AI_OPERATION_NAME: operation, GA.GEN_AI_REQUEST_MODEL: model, GA.GEN_AI_PROVIDER_NAME: provider}
    if temperature is not None:
        attrs[GA.GEN_AI_REQUEST_TEMPERATURE] = temperature
    if max_tokens is not None:
        attrs[GA.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
    started = _clock()
    with _span(f"{operation} {model}", SpanKind.CLIENT, attrs) as span:
        call = LLMCall(span, model, provider, operation)
        try:
            yield call
        except BaseException as e:
            call.error = type(e).__qualname__
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, f"{call.error}: {e}"[:300]))
            span.set_attribute(EA.ERROR_TYPE, call.error)
            raise
        finally:
            u = call._usage
            billed = call.response_model or model
            cost = _config.prices.cost(billed, u, dt.date.today()) if _config.prices else None
            if cost is None and _config.prices:
                cost = _config.prices.cost(model, u, dt.date.today())
            call.cost = cost
            span.set_attributes({GA.GEN_AI_USAGE_INPUT_TOKENS: u.input_tokens, GA.GEN_AI_USAGE_OUTPUT_TOKENS: u.output_tokens})
            if u.cache_read_tokens:
                span.set_attribute(GA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, u.cache_read_tokens)
            if u.cache_write_tokens:
                span.set_attribute(GA.GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS, u.cache_write_tokens)
            if cost is not None:
                span.set_attribute(COST, round(cost, 8))
            labels: dict[str, Any] = {
                GA.GEN_AI_OPERATION_NAME: operation,
                GA.GEN_AI_PROVIDER_NAME: provider,
                GA.GEN_AI_REQUEST_MODEL: model,
            }
            t = _current.get()
            if t is not None:
                labels[GA.GEN_AI_AGENT_NAME] = t.name
                t._add_llm(cost, u)
            i = _instruments()
            i.duration.record((_clock() - started) / 1e9, labels | ({EA.ERROR_TYPE: call.error} if call.error else {}))
            i.tokens.record(u.input_tokens, labels | {GA.GEN_AI_TOKEN_TYPE: "input"})
            i.tokens.record(u.output_tokens, labels | {GA.GEN_AI_TOKEN_TYPE: "output"})
            if cost is not None:
                i.call_cost.add(cost, labels)
            else:
                i.unpriced.add(u.input_tokens + u.output_tokens, labels)


@contextmanager
def tool(name: str, *, call_id: str | None = None, tool_type: str = "function") -> Iterator[Span]:
    attrs: dict[str, Any] = {GA.GEN_AI_OPERATION_NAME: "execute_tool", GA.GEN_AI_TOOL_NAME: name, GA.GEN_AI_TOOL_TYPE: tool_type}
    if call_id:
        attrs[GA.GEN_AI_TOOL_CALL_ID] = call_id
    started = _clock()
    error: str | None = None
    with _span(f"execute_tool {name}", SpanKind.INTERNAL, attrs) as span:
        try:
            yield span
        except BaseException as e:
            error = type(e).__qualname__
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, f"{error}: {e}"[:300]))
            span.set_attribute(EA.ERROR_TYPE, error)
            raise
        finally:
            t = _current.get()
            if t is not None:
                t._add_tool()
            labels: dict[str, Any] = {GA.GEN_AI_TOOL_NAME: name} | ({EA.ERROR_TYPE: error} if error else {})
            if t is not None:
                labels[GA.GEN_AI_AGENT_NAME] = t.name
            _instruments().tool_duration.record((_clock() - started) / 1e9, labels)


@contextmanager
def step(name: str, **attributes: Any) -> Iterator[Span]:
    with _span(name, SpanKind.INTERNAL, {STEP_NAME: name, **attributes}) as span:
        try:
            yield span
        except BaseException as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, f"{type(e).__name__}: {e}"[:300]))
            raise
