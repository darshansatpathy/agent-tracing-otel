from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agenttrace

ROOT = Path(__file__).resolve().parents[1]

# OpenTelemetry allows setting global providers once per process; tests share one pair and clear between tests.
_spans = InMemorySpanExporter()
_reader = InMemoryMetricReader()
_tp = TracerProvider()
_tp.add_span_processor(SimpleSpanProcessor(_spans))
trace.set_tracer_provider(_tp)
metrics.set_meter_provider(MeterProvider(metric_readers=[_reader]))


class Telemetry:
    def __init__(self) -> None:
        self.exporter, self.reader = _spans, _reader

    def spans(self):
        return list(self.exporter.get_finished_spans())

    def by_name(self, prefix: str):
        return [s for s in self.spans() if s.name.startswith(prefix)]

    def metric_points(self, name: str):
        data = self.reader.get_metrics_data()
        out = []
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == name:
                        out += list(m.data.data_points)
        return out


@pytest.fixture
def otel() -> Iterator[Telemetry]:
    _spans.clear()
    _reader.get_metrics_data()  # drain
    agenttrace.configure(prices=ROOT / "prices.yaml", reset_instruments=True)
    yield Telemetry()
    agenttrace.configure(reset_instruments=True)
