# 2. The headline number is cost per completed task

Status: accepted

## Context

Cost per model call and cost per token are easy to compute and misleading. A task that times out after two large-model calls, retries, and then escalates to a person spent money and delivered nothing. Averaging over calls, or over attempted tasks, hides that waste; it shows up only when spend is divided by work actually done.

## Decision

- Cost is computed where the call happens, from the usage the provider returned and a dated price table (cached input billed at the cache rate), and stored on the span (`agent.cost_usd`). A model with no price is counted as unpriced, never as free, and raises an alert.
- The task span accumulates the cost, tokens and calls of everything inside it, including failed calls, and records an outcome: success, failure, escalated or abandoned.
- Cost per completed task = all spend / tasks with outcome success. Dashboards and alerts lead with it; the report breaks it down by agent, model, step, and spend on tasks that did not complete.

## Consequences

"Completed" is the agent's own judgement through `t.outcome(...)`. Teams should set it from the same signal they use for success elsewhere (the ticket closed, the user accepted the answer).
