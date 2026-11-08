# 1. Follow the OpenTelemetry GenAI semantic conventions; extend only under `agent.*`

Status: accepted

## Context

Every tracing vendor for LLM apps invented its own attribute names. Instrumenting to one of them ties the traces to that vendor. OpenTelemetry now defines conventions for model calls, agents and tools (`gen_ai.*`), which Langfuse, Tempo, and most APM vendors read.

## Decision

- Spans: `invoke_agent {agent}` for a task, `chat {model}` (or `text_completion`, `embeddings`) for model calls, `execute_tool {tool}` for tools, with the standard attributes: `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.provider.name`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.response.finish_reasons`, `gen_ai.tool.name`, `error.type`.
- Metrics: the standard `gen_ai.client.token.usage` and `gen_ai.client.operation.duration` histograms, with the conventions' bucket boundaries.
- What the conventions do not cover (cost, task outcome, per-task totals) goes under one prefix, `agent.*`, so it is obvious what is ours.

## Consequences

The GenAI conventions are still marked as development and may rename attributes. Names are constants from the `opentelemetry-semantic-conventions` package, so an upgrade is a dependency bump plus a review of the dashboards and rules, which the tests run through promtool.
