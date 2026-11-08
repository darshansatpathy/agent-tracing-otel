# 3. Sample traces at the tail; never sample the cost metrics

Status: accepted

## Context

Agent traces are large (tens of spans per task) and most are uninteresting. Keeping all of them is expensive; sampling them at the head drops exactly the failures you need. But if cost figures are computed from sampled traces, they are wrong.

## Decision

- The SDK emits cost, token and task metrics directly, from every task, before any sampling. Dashboards and alerts read only these metrics.
- The Collector tail-samples traces after the whole trace has arrived: it keeps every trace with an error, every task that did not complete, every task slower than 60 s, every task costing more than $0.10, and 20% of the rest.
- The Collector deletes prompt and completion attributes before export, whatever the application did.

## Consequences

Some traces behind a metric point are gone; exemplars link the ones that were kept. The report command can run on sampled spans for the breakdowns, but totals come from the metrics.
