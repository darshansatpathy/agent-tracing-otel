"""The deployment files: dashboards in sync with their generator, queries and rules valid PromQL, the collector config coherent."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest
import yaml
from conftest import ROOT

sys.path.insert(0, str(ROOT / "deploy" / "grafana"))
import build

PROMTOOL = shutil.which("promtool") or (shutil.which("/opt/bin/promtool"))


def test_dashboard_is_generated_from_build_py():
    committed = json.loads((ROOT / "deploy/grafana/dashboards/agent-cost.json").read_text())
    assert committed == build.build(), "run python deploy/grafana/build.py"


def test_dashboard_layout_fits_the_grid():
    for p in build.build()["panels"]:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24


@pytest.mark.skipif(PROMTOOL is None, reason="promtool not installed")
def test_rules_and_dashboard_queries_parse(tmp_path):
    rules = ROOT / "deploy/prometheus"
    assert subprocess.run([PROMTOOL, "check", "rules", str(rules / "rules.yaml")], capture_output=True).returncode == 0
    assert subprocess.run([PROMTOOL, "test", "rules", str(rules / "rules_test.yaml")], capture_output=True, cwd=rules).returncode == 0
    qs = [q.replace("$__rate_interval", "5m").replace("$agent", ".*") for q in build.queries()]
    f = tmp_path / "q.yaml"
    f.write_text(yaml.safe_dump({"groups": [{"name": "q", "rules": [{"record": f"q:{i}", "expr": q} for i, q in enumerate(qs)]}]}))
    out = subprocess.run([PROMTOOL, "check", "rules", str(f)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_collector_pipeline_drops_content_and_keeps_metrics_unsampled():
    c = yaml.safe_load((ROOT / "deploy/collector/collector.yaml").read_text())
    traces = c["service"]["pipelines"]["traces"]
    assert traces["processors"].index("attributes/drop_content") < traces["processors"].index("tail_sampling")
    assert "tail_sampling" not in c["service"]["pipelines"]["metrics"]["processors"]
    dropped = {a["key"] for a in c["processors"]["attributes/drop_content"]["actions"]}
    assert {"gen_ai.input.messages", "gen_ai.output.messages"} <= dropped
    for section in ("processors", "exporters"):
        used = {x for p in c["service"]["pipelines"].values() for x in p[section]}
        assert used <= set(c[section]), f"undefined {section}"
    policies = {p["name"] for p in c["processors"]["tail_sampling"]["policies"]}
    assert {"errors", "not-completed", "expensive"} <= policies
