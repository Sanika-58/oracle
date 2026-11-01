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
Pick service + fault + start time T. Fault propagates upstream
through the call chain. Ground truth saved to data/labels.json
(root_service, fault_type, start_time).

## Outputs
- data/logs/<service>.log
- data/traces.json (Jaeger-like)
- data/labels.json

## Pipeline
1. ingest: normalize logs
2. anomaly: per-service detection (z-score first, LSTM later)
3. graph: causal graph from traces (NetworkX)
4. llm: graph-constrained incident report
5. eval: accuracy, MTTD, chain precision