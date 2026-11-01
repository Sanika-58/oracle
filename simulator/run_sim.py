"""Generate synthetic service logs, traces, and incident ground truth."""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faults import choose_incident, onset_map, propagation
from services import CHILDREN, MEANS, SERVICES, iso_time, process_id, process_map, render_log

BASE_EPOCH = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()


def _span_id(rng: random.Random) -> str:
    """Create a trace-compatible 64-bit hexadecimal span ID."""
    return f"{rng.getrandbits(64):016x}"


def _make_run(run_dir: Path, seed: int, duration: int, incident: bool,
              root: str | None = None, fault: str | None = None) -> dict[str, Any]:
    """Simulate one run and write its service logs, traces, and labels."""
    rng = random.Random(seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir = run_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_lines: dict[str, list[str]] = {s: [] for s in SERVICES}
    traces: list[dict[str, Any]] = []

    start = rng.uniform(600, 1200) if incident else 0.0
    fault_duration = 300.0
    chain = propagation(root, {}) if incident and root else []
    lags = {service: rng.uniform(5, 15) for service in chain[1:]}
    onsets = onset_map(chain, start, lags) if chain else {}
    # Recovery follows the same per-hop lag as propagation.
    ends = {service: start + fault_duration + (onsets[service] - start) for service in chain}

    now = 0.0
    while now < duration:
        now += rng.expovariate(10.0)
        if now >= duration:
            break
        trace_id = f"{rng.getrandbits(128):032x}"
        root_service = "api-gateway"
        if rng.random() < 0.30:
            request_path = ("api-gateway", "user-service")
        else:
            request_path = ("api-gateway", "payment-service", "auth-service", "database-proxy")
        spans: list[dict[str, Any]] = []
        outcomes: dict[str, tuple[float, int, bool, str]] = {}
        span_ids = {service: _span_id(rng) for service in request_path}
        for service in reversed(request_path):
            own = max(1.0, rng.gauss(MEANS[service], MEANS[service] * 0.25))
            status, failed, message, level = 200, False, "request completed", "INFO"
            # A gateway may serve healthy branches alongside the affected one.
            is_target = service in onsets and root in request_path
            active = is_target and onsets[service] <= now < ends[service]
            if active:
                if service == root:
                    if fault == "latency_spike":
                        own *= rng.uniform(10, 20)
                        message, level = "latency spike", "WARN"
                    elif fault == "error_burst":
                        if rng.random() < rng.uniform(0.60, 0.90):
                            status, failed, message, level = 500, True, "injected error burst", "ERROR"
                    elif fault == "connection_pool_exhaustion":
                        own *= rng.uniform(2, 6)
                        if rng.random() < rng.uniform(0.80, 0.95):
                            status, failed, message, level = 503, True, "max_connections limit reached", "FATAL"
                    elif fault == "crash":
                        own = 1.0
                        status, failed, message, level = 503, True, "connection refused", "FATAL"
                else:
                    own += rng.uniform(100, 900)
            elif service in onsets and now < onsets[service] and service != root:
                own += rng.uniform(5, 30)  # downstream trouble is initially absorbed
            elif rng.random() < 0.01:
                status, failed, message, level = 500, True, "baseline internal error", "ERROR"
            elif level == "INFO" and rng.random() < 0.04:
                message, level = "request handled", "DEBUG"

            children = [child for child in CHILDREN[service] if child in outcomes]
            child_duration = sum(outcomes[c][0] for c in children)
            child_failed = any(outcomes[c][2] for c in children)
            if active and service != root:
                if service == "api-gateway":
                    status, failed, message, level = 503, True, "upstream returned 503", "ERROR"
                else:
                    dependency = request_path[request_path.index(service) + 1]
                    status, failed, message, level = 504, True, f"timeout calling {dependency}", "ERROR"
            if child_failed and not failed and service not in onsets:
                own += 10.0
            total = own + child_duration
            outcomes[service] = (total, status, failed, message)
            parent = request_path[request_path.index(service) - 1] if request_path.index(service) > 0 else None
            spans.append({"traceID": trace_id, "spanID": span_ids[service], "operationName": service,
                          "references": ([{"refType": "CHILD_OF", "spanID": span_ids[parent]}] if parent else []),
                          "startTime": int((BASE_EPOCH + now) * 1_000_000),
                          "duration": int(total * 1000), "processID": process_id(service),
                          "tags": [{"key": "error", "value": failed}, {"key": "http.status_code", "value": status}]})
            upstream = request_path[request_path.index(service) + 1] if request_path.index(service) + 1 < len(request_path) else None
            log_lines[service].append(render_log(service, BASE_EPOCH + now, level, trace_id, span_ids[service], message, total, status, upstream))
        spans.reverse()
        traces.append({"traceID": trace_id, "spans": spans, "processes": process_map()})

    for service, lines in log_lines.items():
        (log_dir / f"{service}.log").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    trace_file = {"data": traces}
    (run_dir / "traces.json").write_text(json.dumps(trace_file, separators=(",", ":")), encoding="utf-8")
    if incident:
        labels = {"run_id": run_dir.name, "seed": seed, "root_service": root, "fault_type": fault,
                  "fault_start": iso_time(BASE_EPOCH + start), "fault_end": iso_time(BASE_EPOCH + start + fault_duration),
                  "propagation_chain": chain,
                  "true_onsets": {s: iso_time(BASE_EPOCH + t) for s, t in onsets.items()},
                  "affected_services": chain,
                  "unaffected_services": [s for s in SERVICES if s not in chain]}
    else:
        labels = {"run_id": run_dir.name, "seed": seed, "root_service": None, "fault_type": None,
                  "fault_start": None, "fault_end": None, "propagation_chain": [], "true_onsets": {},
                  "affected_services": [], "unaffected_services": list(SERVICES)}
    (run_dir / "labels.json").write_text(json.dumps(labels, indent=2), encoding="utf-8")
    return {"run_id": run_dir.name, "root_service": root, "fault_type": fault,
            "fault_start": labels["fault_start"], "num_traces": len(traces),
            "num_log_lines": sum(map(len, log_lines.values()))}


def main() -> None:
    """Parse arguments, generate runs, and print a compact summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()
    started = time.perf_counter()
    summaries = []
    schedule_rng = random.Random(args.seed)
    for index in range(args.runs):
        root, fault = choose_incident(schedule_rng, index)
        summaries.append(_make_run(args.out / "runs" / f"run_{index + 1:03d}", args.seed + index, args.duration, True, root, fault))
    _make_run(args.out / "baseline", args.seed ^ 0xB451, 3600, False)
    print("run_id    root_service     fault_type                    fault_start                  traces  log_lines")
    for row in summaries:
        print(f"{row['run_id']:<9} {str(row['root_service']):<16} {str(row['fault_type']):<29} {str(row['fault_start']):<28} {row['num_traces']:>6}  {row['num_log_lines']:>9}")
    print(f"Generated {len(summaries)} incident runs and baseline in {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()
