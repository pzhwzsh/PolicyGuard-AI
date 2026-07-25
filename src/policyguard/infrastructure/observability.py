"""Bounded metrics, structured logs, and optional OpenTelemetry export."""

import json
import logging
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from threading import Lock

LOGGER = logging.getLogger("policyguard.http")


def configure_logging(level: str, *, json_logs: bool) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
    if json_logs:
        LOGGER.propagate = False
        if not LOGGER.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            LOGGER.addHandler(handler)
        LOGGER.setLevel(getattr(logging, level.upper(), logging.INFO))


def log_request(**fields: object) -> None:
    LOGGER.info(json.dumps(fields, ensure_ascii=True, separators=(",", ":")))


@dataclass(frozen=True, slots=True)
class MetricKey:
    method: str
    route: str
    status: str


class PrometheusRegistry:
    """Process-local counters with route templates to prevent cardinality explosions."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[MetricKey, int] = defaultdict(int)
        self._duration_seconds: dict[MetricKey, float] = defaultdict(float)
        self._active = 0

    def begin(self) -> None:
        with self._lock:
            self._active += 1

    def finish(self, method: str, route: str, status_code: int, duration_seconds: float) -> None:
        key = MetricKey(method, route, str(status_code))
        with self._lock:
            self._active = max(self._active - 1, 0)
            self._requests[key] += 1
            self._duration_seconds[key] += duration_seconds

    def render(self) -> str:
        lines = [
            "# HELP policyguard_http_requests_total Completed HTTP requests.",
            "# TYPE policyguard_http_requests_total counter",
        ]
        with self._lock:
            for key in sorted(
                self._requests, key=lambda item: (item.route, item.method, item.status)
            ):
                labels = f'method="{key.method}",route="{key.route}",status="{key.status}"'
                lines.append(f"policyguard_http_requests_total{{{labels}}} {self._requests[key]}")
            lines.extend([
                "# HELP policyguard_http_request_duration_seconds_sum Request duration sum.",
                "# TYPE policyguard_http_request_duration_seconds_sum counter",
            ])
            for key in sorted(
                self._duration_seconds, key=lambda item: (item.route, item.method, item.status)
            ):
                labels = f'method="{key.method}",route="{key.route}",status="{key.status}"'
                lines.append(
                    f"policyguard_http_request_duration_seconds_sum{{{labels}}} "
                    f"{self._duration_seconds[key]:.6f}"
                )
            lines.extend([
                "# HELP policyguard_http_requests_active In-flight HTTP requests.",
                "# TYPE policyguard_http_requests_active gauge",
                f"policyguard_http_requests_active {self._active}",
            ])
        return "\n".join(lines) + "\n"


def configure_otel(endpoint: str, service_name: str):
    if not endpoint:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise RuntimeError("otel_dependencies_required") from exc
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


def request_span(tracer, name: str):
    return tracer.start_as_current_span(name) if tracer is not None else nullcontext()
