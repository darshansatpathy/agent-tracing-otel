"""OpenTelemetry tracing for agent runs, with cost per task.

from agenttrace import task, llm, tool

with task("support-triage", task_id="T-1") as t:
    with llm("small-model", provider="openai-compatible") as call:
        ...
        call.usage(input_tokens=812, output_tokens=64)
    with tool("search_kb"):
        ...
    t.outcome("success")
"""

from agenttrace.tracing import LLMCall, Task, configure, llm, step, task, tool

__version__ = "0.1.0"
__all__ = ["LLMCall", "Task", "configure", "llm", "step", "task", "tool"]
