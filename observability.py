"""
OpenTelemetry instrumentation for tracing, metrics, and log correlation.

Exports traces and logs to Grafana Cloud (or any OTLP-compatible backend).
Auto-instruments FastAPI, httpx, Python logging, and Neo4j driver queries.
No changes to database code required.

Configuration (environment variables):
    OTEL_ENABLED            - set to "true" to activate (default: false)
    OTEL_SERVICE_NAME       - logical service name shown in Grafana (default: openpecha-api)
    OTEL_EXPORTER_OTLP_ENDPOINT - Grafana Cloud OTLP endpoint, e.g.
                                  https://otlp-gateway-prod-us-east-0.grafana.net/otlp
    OTEL_EXPORTER_OTLP_HEADERS  - auth header, e.g.
                                  Authorization=Basic <base64(instanceId:token)>
"""

import logging
import re
from collections.abc import Callable
from functools import wraps
from types import CoroutineType
from typing import Any, LiteralString

from fastapi import FastAPI
from neo4j import AsyncManagedTransaction, AsyncResult, AsyncSession, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import StatusCode

from config import settings

logger = logging.getLogger(__name__)

_state: dict[str, bool] = {"neo4j_patched": False}


def _extract_operation(query: str) -> str:
    normalized = " ".join(query.strip().split())
    for keyword in ("MATCH", "CREATE", "MERGE", "DELETE", "RETURN", "CALL", "WITH"):
        if normalized.upper().startswith(keyword):
            # Try to extract the node label, e.g. MATCH (e:Text ...) -> "MATCH Text"
            label_match = re.search(r"\(\w*:(\w+)", normalized)
            if label_match:
                return f"{keyword} {label_match.group(1)}"
            return keyword
    return "query"


_original_session_run = AsyncSession.run
_original_tx_run = AsyncManagedTransaction.run


async def _traced_session_run(
    self: AsyncSession,
    query: LiteralString | Query,
    parameters: dict[str, Any] | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> AsyncResult:
    merged = {**parameters, **kwargs} if parameters else (kwargs or None)
    return await _traced_run("session", _original_session_run, self, query, merged)


async def _traced_tx_run(
    self: AsyncManagedTransaction,
    query: LiteralString,
    parameters: dict[str, Any] | None = None,
    **kwparameters: Any,  # noqa: ANN401
) -> AsyncResult:
    merged = {**parameters, **kwparameters} if parameters else (kwparameters or None)
    return await _traced_run("transaction", _original_tx_run, self, query, merged)


async def _traced_run(
    source: str,
    original_fn: Callable[..., Any],
    self_arg: AsyncSession | AsyncManagedTransaction,
    query: LiteralString | Query,
    parameters: dict[str, Any] | None = None,
) -> AsyncResult:
    tracer = trace.get_tracer("openpecha-api")
    query_str = str(query)
    operation = _extract_operation(query_str)
    truncated_query = query_str[:500] + "..." if len(query_str) > 500 else query_str

    with tracer.start_as_current_span(
        f"neo4j dispatch {operation}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            "db.system": "neo4j",
            "db.name": settings.neo4j_database,
            "db.operation.name": operation,
            "db.query.text": truncated_query,
            "db.neo4j.source": source,
        },
    ) as span:
        try:
            return await original_fn(self_arg, query, parameters)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def _trace_transaction[**P, T](
    operation: str, original: Callable[P, CoroutineType[Any, Any, T]]
) -> Callable[P, CoroutineType[Any, Any, T]]:
    @wraps(original)
    async def measured(*args: P.args, **kwargs: P.kwargs) -> T:
        # execute_read/write return only after lazy results, commit, and retries finish.
        with trace.get_tracer("openpecha-api").start_as_current_span(
            f"neo4j transaction {operation}",
            kind=trace.SpanKind.CLIENT,
            attributes={"db.system": "neo4j", "db.name": settings.neo4j_database},
        ):
            return await original(*args, **kwargs)

    return measured


def _instrument_neo4j_driver() -> None:
    if _state["neo4j_patched"]:
        return

    AsyncSession.run = _traced_session_run  # ty: ignore[invalid-assignment]
    AsyncManagedTransaction.run = _traced_tx_run  # ty: ignore[invalid-assignment]

    AsyncSession.execute_read = _trace_transaction("read", AsyncSession.execute_read)  # ty: ignore[invalid-assignment]
    AsyncSession.execute_write = _trace_transaction("write", AsyncSession.execute_write)  # ty: ignore[invalid-assignment]
    _state["neo4j_patched"] = True
    logger.info("Neo4j AsyncSession.run() and AsyncManagedTransaction.run() instrumented for tracing")


def setup_telemetry(app: FastAPI) -> None:
    if not settings.otel_enabled:
        logger.info("OpenTelemetry disabled (set OTEL_ENABLED=true to activate)")
        return

    service_name = settings.otel_service_name
    environment = settings.environment or "development"

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "2.13.0",
            "deployment.environment": environment,
        }
    )

    provider = TracerProvider(resource=resource)

    otlp_endpoint = settings.otel_exporter_otlp_endpoint
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        logger.info("OpenTelemetry exporting traces to %s", otlp_endpoint)
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("OpenTelemetry exporting traces to console (no OTLP endpoint configured)")

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="__/health,docs,redoc,openapi.json",
    )

    HTTPXClientInstrumentor().instrument()

    LoggingInstrumentor().instrument(set_logging_format=True)

    _instrument_neo4j_driver()

    logger.info(
        "OpenTelemetry initialized: service=%s, environment=%s",
        service_name,
        environment,
    )


def shutdown_telemetry() -> None:
    """Flush and shut down the tracer provider. Call during app shutdown."""
    if not settings.otel_enabled:
        return

    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
        logger.info("OpenTelemetry shut down")
