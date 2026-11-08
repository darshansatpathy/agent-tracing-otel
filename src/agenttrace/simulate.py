"""A synthetic workload: two agents, three models, tools that fail, retries and escalations.

It drives the real instrumentation with a virtual clock, so a thousand tasks produce realistic spans,
durations and metrics in a second. Used for the demo stack, the report in docs/, and tests.
"""

from __future__ import annotations

import contextlib
import random
from dataclasses import dataclass

from agenttrace import tracing
from agenttrace.tracing import llm, step, task, tool


class ModelTimeout(Exception):
    pass


class ToolError(Exception):
    pass


@dataclass
class VirtualClock:
    now_ns: int = 1_790_000_000 * 10**9

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, seconds: float) -> None:
        self.now_ns += int(seconds * 1e9)


def _model_call(
    clock: VirtualClock, rnd: random.Random, model: str, inp: int, out: int, *, cached: int = 0, timeout_rate: float = 0.0
) -> None:
    with llm(model, temperature=0.2) as call:
        speed = {"small-model": 0.004, "large-model": 0.02, "reasoning-model": 0.05}.get(model, 0.01)
        if rnd.random() < timeout_rate:
            clock.advance(30)
            raise ModelTimeout(f"{model} did not answer in 30 s")
        clock.advance(0.25 + out * speed * rnd.uniform(0.7, 1.4))
        call.usage(input_tokens=inp, output_tokens=out, cache_read_tokens=cached)
        call.response(model=model, finish_reasons=["stop"], response_id=f"resp-{rnd.getrandbits(48):x}")


def _with_retry(clock: VirtualClock, rnd: random.Random, attempts: int, fn: object, *args: object, **kw: object) -> None:
    for i in range(attempts):
        try:
            fn(clock, rnd, *args, **kw)  # type: ignore[operator]
            return
        except (ModelTimeout, ToolError):
            if i == attempts - 1:
                raise
            clock.advance(0.5 * 2**i)


def support_triage(clock: VirtualClock, rnd: random.Random, n: int) -> None:
    with task("support-triage", task_id=f"ticket-{n}", conversation_id=f"conv-{n // 3}") as t:
        with step("classify"):
            _model_call(clock, rnd, "small-model", rnd.randint(500, 900), rnd.randint(10, 40))
        with step("retrieve"):

            def search(clock: VirtualClock, rnd: random.Random) -> None:
                with tool("search_kb", call_id=f"call-{rnd.getrandbits(32):x}"):
                    clock.advance(rnd.uniform(0.03, 0.15))
                    if rnd.random() < 0.06:
                        raise ToolError("search backend returned 503")

            _with_retry(clock, rnd, 2, search)
        with step("draft"):
            prompt = rnd.randint(3000, 7000)
            _with_retry(
                clock,
                rnd,
                2,
                _model_call,
                "large-model",
                prompt,
                rnd.randint(250, 700),
                cached=int(prompt * rnd.uniform(0.3, 0.7)),
                timeout_rate=0.03,
            )
        with step("check"):
            with tool("policy_check"):
                clock.advance(rnd.uniform(0.01, 0.05))
                flagged = rnd.random() < 0.10
            if flagged:
                _model_call(clock, rnd, "large-model", rnd.randint(4000, 8000), rnd.randint(300, 700))
                if rnd.random() < 0.5:
                    t.outcome("escalated", "policy check flagged the draft twice")
                    return
        t.outcome("success")


def research(clock: VirtualClock, rnd: random.Random, n: int) -> None:
    with task("account-research", task_id=f"research-{n}") as t:
        with step("plan"):
            _model_call(clock, rnd, "reasoning-model", rnd.randint(2000, 4000), rnd.randint(800, 2500))
        for i in range(rnd.randint(2, 5)):
            with step("gather"), tool("crm_lookup" if i % 2 else "web_fetch"):
                clock.advance(rnd.uniform(0.2, 1.5))
        with step("summarise"):
            _model_call(clock, rnd, "large-model", rnd.randint(6000, 15000), rnd.randint(400, 1200), timeout_rate=0.05)
        if rnd.random() < 0.12:
            t.outcome("abandoned", "user closed the session")
            return
        t.outcome("success")


def run(n_tasks: int = 1000, seed: int = 42) -> VirtualClock:
    """Run the workload against whatever tracer and meter providers are installed."""
    rnd = random.Random(seed)
    clock = VirtualClock()
    tracing.set_clock(clock)
    try:
        for n in range(n_tasks):
            clock.advance(rnd.expovariate(1 / 3.0))
            with contextlib.suppress(ModelTimeout, ToolError):  # the task span already recorded the failure
                (research if rnd.random() < 0.15 else support_triage)(clock, rnd, n)
    finally:
        tracing.set_clock(None)
    return clock
