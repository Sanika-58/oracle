# ORACLE: Log Intelligence Agent for Root Cause Attribution

## Goal
Diagnose cascading failures in microservice apps from logs and traces.

## Simulated services
api-gateway -> user-service
api-gateway -> payment-service -> auth-service -> database-proxy

## Log formats
- api-gateway: JSON
- payment-service: plain text
- auth-service: key=value
- user-service: JSON (different field names)
- database-proxy: plain text

## Unified schema
timestamp, service_id, severity, trace_id, span_id, message,
parsed_fields {latency_ms, status_code, dependency_called, error_type}

## Traffic
~10 requests/sec, baseline error rate ~1%

## Fault types
latency spike, error burst, connection pool exhaustion, crash

## Fault injection
Pick a service and fault type at time T. The fault propagates upstream
with a separate 5–15 second lag per hop. Ground truth is written alongside
each run in `labels.json` with the true onset time for every affected service.

## Running the simulator

Generate reproducible incident runs plus a one-hour fault-free baseline:

```bash
python simulator/run_sim.py --runs 20 --seed 42 --duration 1800 --out data
```

Each incident is written to `data/runs/run_001/` (and subsequent numbered
folders), with per-service logs in `logs/`, Jaeger-like `traces.json`, and
ground-truth `labels.json`. The baseline uses the same layout in
`data/baseline/`. The simulator uses a virtual clock, so simulated time does
not require waiting. Quick checks can be run with
`python simulator/test_sim.py`.

## Pipeline
1. ingest: normalize logs
2. anomaly: per-service detection (z-score first, LSTM later)
3. graph: causal graph from traces (NetworkX)
4. llm: graph-constrained incident report
5. eval: accuracy, MTTD, chain precision
