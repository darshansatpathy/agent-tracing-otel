from __future__ import annotations

import json

import pytest
from conftest import ROOT

from agenttrace import analysis
from agenttrace.cli import main
from agenttrace.export import read


def test_simulation_report(tmp_path, capsys):
    spans = tmp_path / "s.jsonl"
    assert main(["simulate", "--tasks", "300", "--jsonl", str(spans), "--prices", str(ROOT / "prices.yaml")]) == 0
    assert main(["report", str(spans), "--json"]) == 0
    s = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert s["tasks"] == 300 and sum(s["outcomes"].values()) == 300
    assert s["cost_per_completed_task"] > s["cost_per_task"] > 0
    assert set(s["by_model"]) == {"large-model", "reasoning-model", "small-model"}
    assert {"plan", "draft", "classify"} <= set(s["by_step"])
    assert set(s["by_agent"]) == {"support-triage", "account-research"}
    assert 0 < s["wasted_share"] < 0.5
    out = tmp_path / "r.md"
    assert main(["report", str(spans), "--out", str(out)]) == 0
    assert "Cost per completed task" in out.read_text()


def _span(tid, sid, parent, name, attrs, start=0, end=10**9):
    return {
        "trace_id": tid,
        "span_id": sid,
        "parent_id": parent,
        "name": name,
        "start_ns": start,
        "end_ns": end,
        "status": "UNSET",
        "attributes": attrs,
    }


def test_cost_per_completed_task_counts_failed_spend():
    spans = [
        _span(
            "a",
            "1",
            None,
            "invoke_agent x",
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "x",
                "agent.task.outcome": "success",
                "agent.task.cost_usd": 0.10,
            },
        ),
        _span("a", "2", "1", "chat m", {"gen_ai.request.model": "m", "agent.cost_usd": 0.10}),
        _span(
            "b",
            "3",
            None,
            "invoke_agent x",
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "x",
                "agent.task.outcome": "failure",
                "agent.task.cost_usd": 0.30,
            },
        ),
        _span("b", "4", "3", "s", {"agent.step.name": "draft"}),
        _span("b", "5", "4", "chat m", {"gen_ai.request.model": "m", "agent.cost_usd": 0.30}),
        _span("c", "6", None, "orphan", {}),
    ]
    s = analysis.summary(analysis.tasks(spans))
    assert s["tasks"] == 2 and s["cost_per_completed_task"] == pytest.approx(0.40) and s["cost_per_task"] == pytest.approx(0.20)
    assert s["wasted"] == {"failure": pytest.approx(0.30)} and s["wasted_share"] == pytest.approx(0.75)
    assert s["by_step"] == {"(top level)": pytest.approx(0.10), "draft": pytest.approx(0.30)}
    assert analysis.summary([]) == {"tasks": 0} and "No tasks" in analysis.markdown({"tasks": 0})


def test_reads_collector_otlp_json(tmp_path):
    doc = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "t1",
                                "spanId": "s1",
                                "name": "invoke_agent x",
                                "startTimeUnixNano": "0",
                                "endTimeUnixNano": "2000000000",
                                "status": {"code": 2},
                                "attributes": [
                                    {"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}},
                                    {"key": "agent.task.cost_usd", "value": {"doubleValue": 0.5}},
                                    {"key": "agent.task.llm_calls", "value": {"intValue": "3"}},
                                    {
                                        "key": "gen_ai.response.finish_reasons",
                                        "value": {"arrayValue": {"values": [{"stringValue": "stop"}]}},
                                    },
                                    {"key": "x", "value": {"boolValue": True}},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    p = tmp_path / "otlp.json"
    p.write_text(json.dumps(doc) + "\n\n")
    (span,) = list(read(p))
    assert (
        span["status"] == "ERROR"
        and span["attributes"]["agent.task.llm_calls"] == 3
        and span["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    )
    rows = analysis.tasks([span])
    assert rows[0].cost == 0.5 and rows[0].seconds == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
