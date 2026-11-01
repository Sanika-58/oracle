"""Service topology and log rendering for synthetic incidents."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
SERVICES = ("api-gateway", "user-service", "payment-service", "auth-service", "database-proxy")
CHILDREN = {"api-gateway": (), "user-service": (), "payment-service": ("auth-service",),
            "auth-service": ("database-proxy",), "database-proxy": ()}
MEANS = {"api-gateway": 12.0, "user-service": 15.0, "payment-service": 20.0,
         "auth-service": 10.0, "database-proxy": 8.0}


def iso_time(epoch: float) -> str:
    """Format a simulated epoch as an ISO8601 UTC timestamp."""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def render_log(service: str, epoch: float, level: str, trace: str, span: str, msg: str,
               latency: float, status: int, upstream: str | None) -> str:
    """Render a service event in its own log format."""
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    local_dt = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    if service == "api-gateway":
        return json.dumps({"ts": iso_time(epoch), "level": level, "trace_id": trace, "span_id": span,
                           "msg": msg, "latency_ms": round(latency, 2), "status": status, "upstream": upstream}, separators=(",", ":"))
    if service == "user-service":
        return json.dumps({"time_ms": int(epoch * 1000), "severity": level, "traceId": trace, "spanId": span,
                           "event": msg, "duration": round(latency, 2), "http_code": status}, separators=(",", ":"))
    if service == "payment-service":
        stamp = local_dt.strftime("%Y-%m-%d %H:%M:%S") + f",{dt.microsecond // 1000:03d} +0530"
        return f"{stamp} {level} [trace={trace} span={span}] {msg} latency={latency:.2f}ms status={status}"
    if service == "auth-service":
        stamp = local_dt.strftime("%d/%b/%Y:%H:%M:%S") + " +0530"
        return f"ts={stamp} level={level} trace={trace} span={span} op=validate_token dep=database-proxy latency={latency:.2f}ms status={status} err={msg.replace(' ', '_')}"
    stamp = dt.strftime("%H:%M:%S") + f".{dt.microsecond // 1000:03d}"
    return f"[{stamp}] {level} conn_pool: {msg} trace={trace} span={span} duration={latency:.2f}ms status={status}"


def process_map() -> dict[str, dict[str, str]]:
    """Return Jaeger process metadata for every service."""
    return {f"p{i + 1}": {"serviceName": name} for i, name in enumerate(SERVICES)}


def process_id(service: str) -> str:
    """Return stable Jaeger process ID for a service."""
    return f"p{SERVICES.index(service) + 1}"
