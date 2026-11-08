"""Turn spans into the numbers that matter for running agents: what a *completed* task costs.

Cost per call hides failures. A task that fails after three model calls still spent the money, and a
retry spends it again. Cost per completed task = all spend / tasks that succeeded.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agenttrace.tracing import COST, STEP_NAME, TASK_COST, TASK_INPUT, TASK_LLM_CALLS, TASK_OUTCOME, TASK_OUTPUT, TASK_TOOL_CALLS


@dataclass
class TaskRow:
    trace_id: str
    agent: str
    outcome: str
    cost: float
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    seconds: float
    by_model: dict[str, float] = field(default_factory=dict)
    by_step: dict[str, float] = field(default_factory=dict)


def tasks(spans: list[dict[str, Any]]) -> list[TaskRow]:
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in spans:
        by_trace[s["trace_id"]].append(s)
    rows = []
    for tid, ss in by_trace.items():
        roots = [s for s in ss if s["attributes"].get("gen_ai.operation.name") == "invoke_agent"]
        if not roots:
            continue
        root = min(roots, key=lambda s: s["start_ns"])
        a = root["attributes"]
        ids = {s["span_id"]: s for s in ss}
        by_model: dict[str, float] = defaultdict(float)
        by_step: dict[str, float] = defaultdict(float)
        for s in ss:
            c = s["attributes"].get(COST)
            if c is None:
                continue
            by_model[str(s["attributes"].get("gen_ai.request.model"))] += float(c)
            parent = ids.get(s["parent_id"] or "")
            while parent is not None and STEP_NAME not in parent["attributes"] and parent is not root:
                parent = ids.get(parent["parent_id"] or "")
            by_step[parent["attributes"][STEP_NAME] if parent is not None and parent is not root else "(top level)"] += float(c)
        rows.append(
            TaskRow(
                tid,
                str(a.get("gen_ai.agent.name")),
                str(a.get(TASK_OUTCOME, "unknown")),
                float(a.get(TASK_COST, 0.0)),
                int(a.get(TASK_INPUT, 0)),
                int(a.get(TASK_OUTPUT, 0)),
                int(a.get(TASK_LLM_CALLS, 0)),
                int(a.get(TASK_TOOL_CALLS, 0)),
                (root["end_ns"] - root["start_ns"]) / 1e9,
                dict(by_model),
                dict(by_step),
            )
        )
    return rows


def summary(rows: list[TaskRow]) -> dict[str, Any]:
    if not rows:
        return {"tasks": 0}
    spend = sum(r.cost for r in rows)
    done = [r for r in rows if r.outcome == "success"]
    outcomes: dict[str, int] = defaultdict(int)
    wasted: dict[str, float] = defaultdict(float)
    for r in rows:
        outcomes[r.outcome] += 1
        if r.outcome != "success":
            wasted[r.outcome] += r.cost
    models: dict[str, float] = defaultdict(float)
    steps: dict[str, float] = defaultdict(float)
    for r in rows:
        for k, v in r.by_model.items():
            models[k] += v
        for k, v in r.by_step.items():
            steps[k] += v
    agents: dict[str, dict[str, float]] = {}
    for name in sorted({r.agent for r in rows}):
        mine = [r for r in rows if r.agent == name]
        ok = [r for r in mine if r.outcome == "success"]
        spent = sum(r.cost for r in mine)
        agents[name] = {
            "tasks": len(mine),
            "success_rate": len(ok) / len(mine),
            "spend": spent,
            "cost_per_completed_task": spent / len(ok) if ok else float("nan"),
        }
    secs = sorted(r.seconds for r in rows)
    costs = sorted(r.cost for r in rows)
    return {
        "tasks": len(rows),
        "outcomes": dict(outcomes),
        "spend": spend,
        "cost_per_task": spend / len(rows),
        "cost_per_completed_task": spend / len(done) if done else float("nan"),
        "median_cost_of_a_success": statistics.median(r.cost for r in done) if done else float("nan"),
        "wasted": dict(wasted),
        "wasted_share": sum(wasted.values()) / spend if spend else 0.0,
        "llm_calls_per_task": sum(r.llm_calls for r in rows) / len(rows),
        "tokens_per_task": sum(r.input_tokens + r.output_tokens for r in rows) / len(rows),
        "p50_seconds": secs[len(secs) // 2],
        "p95_seconds": secs[int(0.95 * (len(secs) - 1))],
        "p95_cost": costs[int(0.95 * (len(costs) - 1))],
        "by_agent": agents,
        "by_model": dict(sorted(models.items(), key=lambda kv: -kv[1])),
        "by_step": dict(sorted(steps.items(), key=lambda kv: -kv[1])),
    }


def markdown(s: dict[str, Any], title: str = "Agent cost report") -> str:
    if not s["tasks"]:
        return f"# {title}\n\nNo tasks found.\n"
    L = [
        f"# {title}",
        "",
        f"**Cost per completed task ${s['cost_per_completed_task']:.4f}**, against ${s['cost_per_task']:.4f} per task attempted and "
        f"${s['median_cost_of_a_success']:.4f} for the median success. {s['wasted_share']:.0%} of spend went to tasks that did not complete.",
        "",
        "| | |",
        "|---|---|",
        f"| Tasks | {s['tasks']:,} ({', '.join(f'{k} {v:,}' for k, v in sorted(s['outcomes'].items()))}) |",
        f"| Spend | ${s['spend']:.2f} |",
        f"| Model calls per task | {s['llm_calls_per_task']:.2f} |",
        f"| Tokens per task | {s['tokens_per_task']:,.0f} |",
        f"| Task duration p50 / p95 | {s['p50_seconds']:.2f} s / {s['p95_seconds']:.2f} s |",
        f"| Cost of the 95th-percentile task | ${s['p95_cost']:.4f} |",
        "",
        "## By agent",
        "",
        "| Agent | Tasks | Completed | Spend | Share | Cost per completed task |",
        "|---|---|---|---|---|---|",
    ]
    L += [
        f"| {k} | {v['tasks']:,.0f} | {v['success_rate']:.0%} | ${v['spend']:.2f} | {v['spend'] / s['spend']:.0%} | ${v['cost_per_completed_task']:.4f} |"
        for k, v in s["by_agent"].items()
    ]
    L += [
        "",
        "## Where the money goes",
        "",
        "| Model | Spend | Share |",
        "|---|---|---|",
    ]
    L += [f"| {k} | ${v:.2f} | {v / s['spend']:.0%} |" for k, v in s["by_model"].items()]
    L += ["", "| Step | Spend | Share |", "|---|---|---|"]
    L += [f"| {k} | ${v:.2f} | {v / s['spend']:.0%} |" for k, v in s["by_step"].items()]
    if s["wasted"]:
        L += ["", "| Spend on tasks that did not complete | |", "|---|---|"]
        L += [f"| {k} | ${v:.2f} |" for k, v in sorted(s["wasted"].items(), key=lambda kv: -kv[1])]
    return "\n".join(L) + "\n"
