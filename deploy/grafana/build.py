"""Generate the Grafana dashboard from one list of panels, so queries live in one reviewed place.

python deploy/grafana/build.py            # writes deploy/grafana/dashboards/agent-cost.json
python deploy/grafana/build.py --queries  # prints every PromQL query (CI parses them with promtool)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
A = 'gen_ai_agent_name=~"$agent"'

# (title, unit, kind, width, [(query, legend)])
PANELS: list[tuple[str, str, str, int, list[tuple[str, str]]]] = [
    ("Cost per completed task", "currencyUSD", "stat", 6, [(f"agent:cost_per_completed_task_usd:1h{{{A}}}", "{{gen_ai_agent_name}}")]),
    ("Spend per hour", "currencyUSD", "stat", 6, [(f"sum(agent:spend_usd:rate1h{{{A}}})", "spend")]),
    ("Tasks completed", "percentunit", "stat", 6, [(f"agent:success_ratio:1h{{{A}}}", "{{gen_ai_agent_name}}")]),
    ("95th-percentile task cost", "currencyUSD", "stat", 6, [(f"agent:task_cost_usd:p95_1h{{{A}}}", "{{gen_ai_agent_name}}")]),
    (
        "Cost per completed task over time",
        "currencyUSD",
        "timeseries",
        12,
        [
            (f"agent:cost_per_completed_task_usd:1h{{{A}}}", "{{gen_ai_agent_name}} (1h)"),
            (f"agent:cost_per_completed_task_usd:7d{{{A}}}", "{{gen_ai_agent_name}} (7d)"),
        ],
    ),
    ("Spend by model", "currencyUSD", "timeseries", 12, [("model:spend_usd:rate1h", "{{gen_ai_request_model}}")]),
    (
        "Task outcomes",
        "short",
        "timeseries",
        12,
        [(f"sum by (agent_task_outcome) (rate(agent_tasks_total{{{A}}}[$__rate_interval])) * 60", "{{agent_task_outcome}} / min")],
    ),
    (
        "Spend on tasks that did not complete",
        "currencyUSD",
        "timeseries",
        12,
        [
            (
                f'sum by (agent_task_outcome) (rate(agent_task_cost_usd_sum{{{A},agent_task_outcome!="success"}}[$__rate_interval])) * 3600',
                "{{agent_task_outcome}} / h",
            )
        ],
    ),
    (
        "Tokens per model call (p50)",
        "short",
        "timeseries",
        12,
        [
            (
                f"histogram_quantile(0.5, sum by (le, gen_ai_token_type) (rate(gen_ai_client_token_usage_bucket{{{A}}}[$__rate_interval])))",
                "{{gen_ai_token_type}}",
            )
        ],
    ),
    (
        "Model call latency p95",
        "s",
        "timeseries",
        12,
        [
            (
                f"histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_duration_seconds_bucket{{{A}}}[$__rate_interval])))",
                "{{gen_ai_request_model}}",
            )
        ],
    ),
    ("Model call errors", "percentunit", "timeseries", 12, [("model:call_error_ratio:15m", "{{gen_ai_request_model}}")]),
    (
        "Tool errors",
        "short",
        "timeseries",
        12,
        [
            (
                f'sum by (gen_ai_tool_name) (rate(agent_tool_duration_seconds_count{{{A},error_type!=""}}[$__rate_interval])) * 60',
                "{{gen_ai_tool_name}} / min",
            )
        ],
    ),
    (
        "Unpriced tokens (spend missing from all figures)",
        "short",
        "timeseries",
        24,
        [("sum by (gen_ai_request_model) (rate(gen_ai_client_unpriced_tokens_total[$__rate_interval]))", "{{gen_ai_request_model}}")],
    ),
]


def queries() -> list[str]:
    return [q for *_, targets in PANELS for q, _ in targets]


def build() -> dict:
    panels, x, y, row_h = [], 0, 0, 0
    for i, (title, unit, kind, w, targets) in enumerate(PANELS):
        h = 4 if kind == "stat" else 8
        if x + w > 24:
            x, y = 0, y + row_h
            row_h = 0
        panels.append(
            {
                "id": i + 1,
                "type": kind,
                "title": title,
                "datasource": PROM,
                "gridPos": {"x": x, "y": y, "w": w, "h": h},
                "fieldConfig": {
                    "defaults": {"unit": unit, "decimals": 4 if unit == "currencyUSD" and kind == "stat" else None},
                    "overrides": [],
                },
                "options": {"legend": {"displayMode": "list", "placement": "bottom"}}
                if kind == "timeseries"
                else {"reduceOptions": {"calcs": ["lastNotNull"]}},
                "targets": [
                    {"refId": chr(65 + j), "expr": q, "legendFormat": leg, "exemplar": kind == "timeseries", "datasource": PROM}
                    for j, (q, leg) in enumerate(targets)
                ],
            }
        )
        x += w
        row_h = max(row_h, h)
    return {
        "uid": "agent-cost",
        "title": "Agents: cost per completed task",
        "tags": ["agents", "genai", "cost"],
        "schemaVersion": 39,
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "1m",
        "templating": {
            "list": [
                {
                    "name": "agent",
                    "type": "query",
                    "datasource": PROM,
                    "query": {"query": "label_values(agent_tasks_total, gen_ai_agent_name)", "refId": "agents"},
                    "includeAll": True,
                    "multi": True,
                    "allValue": ".*",
                    "current": {"text": "All", "value": "$__all"},
                }
            ]
        },
        "links": [{"title": "Traces in Tempo", "type": "link", "url": "/explore?left=%7B%22datasource%22:%22tempo%22%7D"}],
        "panels": panels,
    }


if __name__ == "__main__":
    if "--queries" in sys.argv:
        print("\n".join(queries()))
    else:
        out = Path(__file__).parent / "dashboards" / "agent-cost.json"
        out.write_text(json.dumps(build(), indent=2) + "\n")
        print(f"wrote {out}")
