# Changelog

## 0.1.0

- `task`, `llm`, `tool` and `step` context managers producing spans in the OpenTelemetry GenAI conventions.
- Cost per call from a dated price table, cache-aware; per-task totals of cost, tokens and calls; task outcomes.
- Metrics: standard token and duration histograms, spend, unpriced tokens, tasks by outcome, task cost and duration.
- Content capture off by default, with redaction and truncation when on.
- Traced helper for OpenAI-compatible chat endpoints.
- `agenttrace simulate` (virtual-clock workload) and `agenttrace report` (cost per completed task by agent, model and step), reading our JSON lines or the Collector's OTLP JSON.
- Collector pipeline with content stripping and tail sampling, a Langfuse overlay, Prometheus recording and alert rules with promtool tests, a generated Grafana dashboard, and a Compose stack.
