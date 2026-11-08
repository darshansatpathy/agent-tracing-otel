"""`agenttrace`: simulate a workload into spans, and report cost per completed task from spans."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agenttrace import __version__


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agenttrace", description=__doc__)
    p.add_argument("--version", action="version", version=f"agenttrace {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate", help="run the synthetic workload and export its spans and metrics")
    s.add_argument("--tasks", type=int, default=1000)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--prices", default="prices.yaml")
    s.add_argument("--jsonl", help="write spans to this file")
    s.add_argument("--otlp", help="also send spans and metrics to this OTLP/HTTP endpoint, e.g. http://localhost:4318")
    r = sub.add_parser("report", help="cost per completed task, by model and step, from spans")
    r.add_argument("spans", help="JSON lines from `simulate --jsonl` or the Collector's file exporter")
    r.add_argument("--out")
    r.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.cmd == "simulate":
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from agenttrace import configure, simulate
        from agenttrace.export import JSONLExporter

        res = Resource.create({"service.name": "agent-workload-simulator", "deployment.environment.name": "demo"})
        tp = TracerProvider(resource=res)
        readers: list[MetricReader] = []
        if a.jsonl:
            Path(a.jsonl).unlink(missing_ok=True)
            tp.add_span_processor(BatchSpanProcessor(JSONLExporter(a.jsonl), max_queue_size=100_000, max_export_batch_size=2048))
        if a.otlp:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{a.otlp}/v1/traces"), max_queue_size=100_000))
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{a.otlp}/v1/metrics"), export_interval_millis=5000))
        mp = MeterProvider(resource=res, metric_readers=readers)
        configure(prices=a.prices, tracer_provider=tp, meter_provider=mp)
        try:
            simulate.run(a.tasks, a.seed)
        finally:
            tp.shutdown()
            mp.shutdown()
            configure(reset_instruments=True)
        print(f"simulated {a.tasks} tasks")
        return 0

    from agenttrace import analysis
    from agenttrace.export import read

    summary = analysis.summary(analysis.tasks(list(read(a.spans))))
    text = json.dumps(summary, indent=2) if a.json else analysis.markdown(summary)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
