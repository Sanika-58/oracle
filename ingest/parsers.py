"""Parsers for the five synthetic service log formats."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
_PAYMENT = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \+\d{4}) "
    r"(?P<level>\S+) \[trace=(?P<trace>\S+) span=(?P<span>\S+)\] "
    r"(?P<message>.*?) latency=(?P<latency>[\d.]+)ms status=(?P<status>\d+)$"
)
_AUTH_TIME = re.compile(r"^ts=(\d{2}/[A-Za-z]{3}/\d{4}:\d\d:\d\d:\d\d \+\d{4}) (.*)$")
_DB = re.compile(
    r"^\[(?P<clock>\d\d:\d\d:\d\d\.\d{3})\] (?P<level>\S+) "
    r"(?P<component>[^:]+): (?P<message>.*?) trace=(?P<trace>\S+) "
    r"span=(?P<span>\S+) duration=(?P<latency>[\d.]+)ms status=(?P<status>\d+)$"
)


def _normalized(timestamp: datetime, level: str, trace: str, span: str, message: str,
                latency: Any, status: Any, dependency: Any = None, error: Any = None) -> dict[str, Any]:
    """Build the common parser result before error taxonomy is applied."""
    return {"timestamp": timestamp.astimezone(UTC), "severity": level, "trace_id": str(trace),
            "span_id": str(span), "message": str(message),
            "latency_ms": _number(latency), "status_code": _integer(status),
            "dependency_called": dependency, "error_hint": error}


def _number(value: Any) -> float | None:
    """Convert a numeric field without raising on absent or malformed input."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    """Convert a status field without raising on absent or malformed input."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_api_gateway(line: str) -> dict[str, Any]:
    """Parse an api-gateway JSON log line with an ISO timestamp."""
    row = json.loads(line)
    stamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("api-gateway timestamp has no timezone")
    return _normalized(stamp, row.get("level", "INFO"), row["trace_id"], row["span_id"],
                       row.get("msg", ""), row.get("latency_ms"), row.get("status"), row.get("upstream"))


def parse_user_service(line: str) -> dict[str, Any]:
    """Parse user-service JSON with epoch milliseconds and its alternate fields."""
    row = json.loads(line)
    stamp = datetime.fromtimestamp(int(row["time_ms"]) / 1000, UTC)
    return _normalized(stamp, row.get("severity", "INFO"), row["traceId"], row["spanId"],
                       row.get("event", ""), row.get("duration"), row.get("http_code"))


def parse_payment_service(line: str) -> dict[str, Any]:
    """Parse payment-service's local-time plain-text format."""
    match = _PAYMENT.fullmatch(line)
    if not match:
        raise ValueError("unrecognized payment-service line")
    stamp = datetime.strptime(match["ts"], "%Y-%m-%d %H:%M:%S,%f %z")
    return _normalized(stamp, match["level"], match["trace"], match["span"], match["message"],
                       match["latency"], match["status"])


def parse_auth_service(line: str) -> dict[str, Any]:
    """Parse auth-service key/value logs with a +0530 timestamp."""
    head = _AUTH_TIME.fullmatch(line)
    if not head:
        raise ValueError("unrecognized auth-service timestamp")
    stamp = datetime.strptime(head[1], "%d/%b/%Y:%H:%M:%S %z")
    fields = dict(re.findall(r"(?:^|\s)(\w+)=([^\s]+)", head[2]))
    required = ("level", "trace", "span", "op", "dep", "latency", "status", "err")
    if any(key not in fields for key in required):
        raise ValueError("auth-service line is missing required fields")
    latency = fields["latency"].removesuffix("ms")
    return _normalized(stamp, fields["level"], fields["trace"], fields["span"],
                       fields["err"].replace("_", " "), latency, fields["status"], fields["dep"], fields["err"])


def parse_database_proxy(line: str, date: datetime,
                         previous: datetime | None = None) -> tuple[dict[str, Any], datetime]:
    """Parse a UTC time-only database line and update its rollover date."""
    match = _DB.fullmatch(line)
    if not match:
        raise ValueError("unrecognized database-proxy line")
    clock = datetime.strptime(match["clock"], "%H:%M:%S.%f").time()
    stamp = datetime.combine(date.date(), clock, UTC)
    if previous is not None and previous - stamp > timedelta(hours=12):
        stamp += timedelta(days=1)
    row = _normalized(stamp, match["level"], match["trace"], match["span"], match["message"],
                      match["latency"], match["status"])
    return row, stamp


PARSERS = {
    "api-gateway": parse_api_gateway,
    "user-service": parse_user_service,
    "payment-service": parse_payment_service,
    "auth-service": parse_auth_service,
}
