"""Normalize service logs into one timestamp-sorted JSONL stream."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from parsers import PARSERS, parse_database_proxy

SERVICES = ("api-gateway", "user-service", "payment-service", "auth-service", "database-proxy")
SEVERITIES = {"DEBUG": "DEBUG", "INFO": "INFO", "WARN": "WARN", "WARNING": "WARN",
              "ERROR": "ERROR", "FATAL": "FATAL", "CRITICAL": "FATAL"}
_CALLING = re.compile(r"\bcalling\s+([a-zA-Z0-9_.-]+)")
_ID = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _earliest_trace_date(run_dir: Path) -> datetime:
    """Get the UTC date of the earliest span start in this run."""
    traces = json.loads((run_dir / "traces.json").read_text(encoding="utf-8"))
    starts = [span["startTime"] for trace in traces["data"] for span in trace["spans"]]
    if not starts:
        raise ValueError(f"no spans in {run_dir / 'traces.json'}")
    return datetime.fromtimestamp(min(starts) / 1_000_000, timezone.utc)


def _error_type(row: dict[str, Any]) -> str:
    """Classify an event using specific error text before its HTTP status."""
    text = f"{row.get('message', '')} {row.get('error_hint') or ''}".lower().replace("_", " ")
    status = row.get("status_code")
    if "connection refused" in text or "connection reset" in text:
        return "connection_refused"
    if "max connections" in text or "connection pool" in text or "pool exhausted" in text:
        return "pool_exhausted"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "baseline" in text or "internal error" in text:
        return "baseline_error"
    if status is not None and status >= 500:
        return "http_5xx"
    return "none"


def _message_template(message: str) -> str:
    """Mask variable IDs and numbers to form a lightweight deduplication key."""
    return _NUMBER.sub("#", _ID.sub("<id>", message.lower()))


def _format_timestamp(value: datetime) -> str:
    """Format an aware timestamp in UTC with millisecond precision."""
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_row(service: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert a service-specific parser result to the public schema."""
    severity = SEVERITIES.get(str(parsed.get("severity", "INFO")).upper(), "INFO")
    dependency = parsed.get("dependency_called")
    if not dependency:
        match = _CALLING.search(str(parsed.get("message", "")))
        dependency = match.group(1) if match else None
    return {"timestamp": _format_timestamp(parsed["timestamp"]), "service_id": service,
            "severity": severity, "trace_id": parsed.get("trace_id"), "span_id": parsed.get("span_id"),
            "message": parsed.get("message", ""),
            "parsed_fields": {"latency_ms": parsed.get("latency_ms"),
                              "status_code": parsed.get("status_code"),
                              "dependency_called": dependency,
                              "error_type": _error_type(parsed)}}


def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse matching service/severity/message templates within one second."""
    records.sort(key=lambda row: row["timestamp"])
    groups: list[dict[str, Any]] = []
    recent: dict[tuple[str, str, str], int] = {}
    for record in records:
        key = (record["service_id"], record["severity"], _message_template(record["message"]))
        index = recent.get(key)
        if index is not None:
            first = datetime.fromisoformat(groups[index]["timestamp"].replace("Z", "+00:00"))
            current = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
            if (current - first).total_seconds() <= 1.0:
                groups[index]["repeat_count"] = groups[index].get("repeat_count", 1) + 1
                continue
        recent[key] = len(groups)
        groups.append(record)
    return groups


def normalize_run(run_dir: Path) -> tuple[int, int, int]:
    """Normalize one run, capturing malformed lines without stopping it."""
    base_date = _earliest_trace_date(run_dir)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    raw_count = 0
    previous_db_time: datetime | None = None
    for service in SERVICES:
        log_path = run_dir / "logs" / f"{service}.log"
        if not log_path.exists():
            errors.append(f"{service}: missing log file {log_path.name}")
            continue
        for line_no, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
            raw_count += 1
            try:
                if service == "database-proxy":
                    parsed, previous_db_time = parse_database_proxy(line, base_date, previous_db_time)
                else:
                    parsed = PARSERS[service](line)
                records.append(_normalize_row(service, parsed))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError, OverflowError) as exc:
                errors.append(f"{service}:{line_no}: {exc}: {line}")

    records = _deduplicate(records)
    out = run_dir / "normalized.jsonl"
    out.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    (run_dir / "normalize_errors.log").write_text("\n".join(errors) + ("\n" if errors else ""), encoding="utf-8")
    return raw_count, sum(row.get("repeat_count", 1) for row in records), len(errors)


def main() -> None:
    """Normalize every incident run and the optional baseline directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    run_root = args.data / "runs"
    run_dirs = sorted(path for path in run_root.iterdir() if path.is_dir()) if run_root.exists() else []
    baseline = args.data / "baseline"
    if baseline.is_dir():
        run_dirs.append(baseline)
    failures = 0
    for run_dir in run_dirs:
        raw, normalized, errors = normalize_run(run_dir)
        failures += errors
        print(f"{run_dir.name}: raw={raw} represented={normalized} parse_errors={errors}")
    if not run_dirs:
        parser.error(f"no runs found under {run_root}")
    print(f"Normalized {len(run_dirs)} directories; parse failures={failures}")


if __name__ == "__main__":
    main()
