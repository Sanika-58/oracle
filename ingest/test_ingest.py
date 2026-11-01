"""Acceptance checks for the Stage 1 log normalizer."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parents[1] / "data"
SERVICES = ("api-gateway", "user-service", "payment-service", "auth-service", "database-proxy")


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Read normalized JSONL records from a run."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _epoch(value: str) -> float:
    """Convert an ISO timestamp to seconds since epoch."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class NormalizerAcceptance(unittest.TestCase):
    """Exercise parse coverage, linkage, ordering, dedup, and incident timing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.run_dirs = sorted(path for path in (DATA / "runs").iterdir() if path.is_dir())
        cls.run_dirs.append(DATA / "baseline")

    def test_parse_failure_rate_and_raw_count_accounting(self) -> None:
        """Require zero parser errors and exact input-line representation."""
        for run_dir in self.run_dirs:
            errors = (run_dir / "normalize_errors.log").read_text(encoding="utf-8")
            self.assertEqual(errors, "", run_dir.name)
            records = _read_records(run_dir / "normalized.jsonl")
            for service in SERVICES:
                raw_count = len((run_dir / "logs" / f"{service}.log").read_text(encoding="utf-8").splitlines())
                represented = sum(row.get("repeat_count", 1) for row in records if row["service_id"] == service)
                self.assertEqual(represented, raw_count, f"{run_dir.name}/{service}")

    def test_trace_linkage(self) -> None:
        """Every retained trace ID belongs to a trace in its own run."""
        for run_dir in self.run_dirs:
            trace_json = json.loads((run_dir / "traces.json").read_text(encoding="utf-8"))
            trace_ids = {trace["traceID"] for trace in trace_json["data"]}
            for row in _read_records(run_dir / "normalized.jsonl"):
                self.assertIn(row["trace_id"], trace_ids)

    def test_timestamp_format_and_sorting(self) -> None:
        """Records use UTC millisecond ISO strings in ascending order."""
        for run_dir in self.run_dirs:
            timestamps = [row["timestamp"] for row in _read_records(run_dir / "normalized.jsonl")]
            for value in timestamps:
                self.assertRegex(value, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))
            self.assertEqual(timestamps, sorted(timestamps), run_dir.name)

    def test_first_failure_at_each_propagation_onset(self) -> None:
        """Check first propagated failures; latency roots correctly begin as WARN."""
        for run_dir in self.run_dirs:
            if run_dir.name == "baseline":
                continue
            labels = json.loads((run_dir / "labels.json").read_text(encoding="utf-8"))
            records = _read_records(run_dir / "normalized.jsonl")
            for service in labels["affected_services"]:
                onset = labels["true_onsets"][service]
                is_latency_root = service == labels["root_service"] and labels["fault_type"] == "latency_spike"
                expected_levels = {"WARN"} if is_latency_root else {"ERROR", "FATAL"}
                candidates = [row for row in records if row["service_id"] == service
                              and row["severity"] in expected_levels and row["timestamp"] >= onset]
                self.assertTrue(candidates, f"{run_dir.name}/{service}: no onset event")
                delay = _epoch(candidates[0]["timestamp"]) - _epoch(onset)
                self.assertGreaterEqual(delay, 0)
                self.assertLessEqual(delay, 1.5, f"{run_dir.name}/{service}: {delay:.3f}s")

    def test_run_001_print_samples_near_fault_start(self) -> None:
        """Print three nearest normalized events per service for the requested run."""
        run_dir = DATA / "runs" / "run_001"
        labels = json.loads((run_dir / "labels.json").read_text(encoding="utf-8"))
        fault_epoch = _epoch(labels["fault_start"])
        records = _read_records(run_dir / "normalized.jsonl")
        print(f"\nNormalized samples around run_001 fault_start={labels['fault_start']}")
        for service in SERVICES:
            nearest = sorted((row for row in records if row["service_id"] == service),
                             key=lambda row: abs(_epoch(row["timestamp"]) - fault_epoch))[:3]
            self.assertEqual(len(nearest), 3)
            print(f"\n{service}:")
            for row in nearest:
                print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
