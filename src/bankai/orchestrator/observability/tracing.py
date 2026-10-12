"""OpenTelemetry tracing. Spans carry ids, agent/tool names, token counts - never prompts or PII."""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from bankai.shared.config import Settings


def setup_tracing(app: FastAPI, settings: Settings) -> None:
    if not settings.otel_exporter_endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    provider = TracerProvider(resource=Resource.create({
        "service.name": settings.service_name, "deployment.environment": settings.env.value}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{settings.otel_exporter_endpoint.rstrip('/')}/v1/traces")))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
