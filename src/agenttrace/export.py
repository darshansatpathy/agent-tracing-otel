"""A JSON-lines span exporter for local runs, tests and offline analysis.

`agenttrace report` reads this format, and also the OpenTelemetry Collector's file exporter output
(OTLP JSON), so the same report runs on spans captured in production.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


def span_record(s: ReadableSpan) -> dict[str, Any]:
    ctx = s.get_span_context()
    assert ctx is not None
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
        "name": s.name,
        "start_ns": s.start_time,
        "end_ns": s.end_time,
        "status": s.status.status_code.name,
        "attributes": dict(s.attributes or {}),
    }


class JSONLExporter(SpanExporter):
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        lines = "".join(json.dumps(span_record(s), default=list) + "\n" for s in spans)
        with self._lock, self.path.open("a") as f:
            f.write(lines)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _otlp_value(v: dict[str, Any]) -> Any:
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_otlp_value(x) for x in v["arrayValue"].get("values", [])]
    return None


def read(path: Path | str) -> Iterator[dict[str, Any]]:
    """Spans from our JSONL, or from OTLP JSON lines written by the Collector's file exporter."""
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        doc = json.loads(line)
        if "resourceSpans" not in doc:
            yield doc
            continue
        for rs in doc["resourceSpans"]:
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    yield {
                        "trace_id": s["traceId"],
                        "span_id": s["spanId"],
                        "parent_id": s.get("parentSpanId") or None,
                        "name": s["name"],
                        "start_ns": int(s["startTimeUnixNano"]),
                        "end_ns": int(s["endTimeUnixNano"]),
                        "status": {"STATUS_CODE_ERROR": "ERROR", 2: "ERROR"}.get(s.get("status", {}).get("code"), "UNSET"),
                        "attributes": {a["key"]: _otlp_value(a["value"]) for a in s.get("attributes", [])},
                    }
