"""Fault selection and lagged service propagation."""

from __future__ import annotations

import random

FAULT_TYPES = ("latency_spike", "error_burst", "connection_pool_exhaustion", "crash")


def propagation(root: str, lags: dict[str, float]) -> list[str]:
    """Build the upstream chain from a fault root to the entry service."""
    parents = {"database-proxy": "auth-service", "auth-service": "payment-service",
               "payment-service": "api-gateway", "user-service": "api-gateway"}
    chain = [root]
    while chain[-1] in parents:
        chain.append(parents[chain[-1]])
    return chain


def onset_map(chain: list[str], start: float, lags: dict[str, float]) -> dict[str, float]:
    """Calculate cumulative lagged anomaly onsets for a propagation chain."""
    result = {chain[0]: start}
    for i in range(1, len(chain)):
        result[chain[i]] = result[chain[i - 1]] + lags[chain[i]]
    return result


def choose_incident(rng: random.Random, run_index: int) -> tuple[str, str]:
    """Cycle through every root and fault type while randomizing pair order."""
    roots = ("api-gateway", "user-service", "payment-service", "auth-service", "database-proxy")
    # A shuffled Cartesian schedule guarantees broad coverage over normal batches.
    pairs = [(root, fault) for root in roots for fault in FAULT_TYPES]
    rng.shuffle(pairs)
    return pairs[run_index % len(pairs)]
