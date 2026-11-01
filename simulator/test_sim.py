"""Quick standard-library sanity checks; run with python simulator/test_sim.py."""

from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from run_sim import _make_run
from services import SERVICES


class SimulatorChecks(unittest.TestCase):
    """Validate deterministic output, linkage, propagation, and formats."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_seed_reproducibility(self) -> None:
        """Same seed and incident produce byte-identical output files."""
        a, b = self.root / "a" / "run_001", self.root / "b" / "run_001"
        _make_run(a, 27, 30, True, "database-proxy", "crash")
        _make_run(b, 27, 30, True, "database-proxy", "crash")
        for rel in ("traces.json", "labels.json", *(f"logs/{s}.log" for s in SERVICES)):
            self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes())

    def test_log_trace_linkage_and_formats(self) -> None:
        """Every event trace ID exists and each log uses its declared syntax."""
        run = self.root / "run"
        _make_run(run, 27, 40, True, "database-proxy", "error_burst")
        trace_ids = {x["traceID"] for x in json.loads((run / "traces.json").read_text())["data"]}
        patterns = {"api-gateway": r'^\{"ts":".*Z","level":', "user-service": r'^\{"time_ms":\d+,"severity":',
                    "payment-service": r'^\d{4}-\d\d-\d\d .* \+0530 ',
                    "auth-service": r'^ts=\d{2}/[A-Za-z]{3}/\d{4}:', "database-proxy": r'^\[\d\d:\d\d:\d\d\.\d{3}\] '}
        for service in SERVICES:
            lines = (run / "logs" / f"{service}.log").read_text().splitlines()
            self.assertTrue(lines)
            for line in lines:
                if service == "api-gateway":
                    trace_id = json.loads(line)["trace_id"]
                elif service == "user-service":
                    trace_id = json.loads(line)["traceId"]
                else:
                    match = re.search(r"trace=([0-9a-f]+)", line)
                    self.assertIsNotNone(match)
                    trace_id = match.group(1)
                self.assertIn(trace_id, trace_ids)
                self.assertRegex(line, patterns[service])

    def test_lagged_onsets(self) -> None:
        """Labels record strictly increasing 5-15 second hop delays."""
        run = self.root / "run"
        _make_run(run, 29, 30, True, "database-proxy", "crash")
        labels = json.loads((run / "labels.json").read_text())
        from datetime import datetime
        values = [datetime.fromisoformat(labels["true_onsets"][s].replace("Z", "+00:00")).timestamp()
                  for s in labels["propagation_chain"]]
        for left, right in zip(values, values[1:]):
            self.assertGreater(right, left)
            self.assertGreaterEqual(right - left, 5)
            self.assertLessEqual(right - left, 15)

    def test_unaffected_baseline_error_rate(self) -> None:
        """An unaffected user path retains roughly its configured error rate."""
        run = self.root / "run"
        _make_run(run, 101, 1800, True, "database-proxy", "crash")
        lines = (run / "logs" / "user-service.log").read_text().splitlines()
        self.assertGreater(len(lines), 200)
        errors = sum(json.loads(line)["http_code"] >= 500 for line in lines)
        self.assertLess(errors / len(lines), 0.04)
        labels = json.loads((run / "labels.json").read_text())
        start = labels["fault_start"]
        end = labels["fault_end"]
        gateway = [json.loads(line) for line in (run / "logs" / "api-gateway.log").read_text().splitlines()]
        user_route = [row for row in gateway if row["upstream"] == "user-service" and start <= row["ts"] <= end]
        self.assertGreater(len(user_route), 20)
        self.assertLess(sum(row["status"] >= 500 for row in user_route) / len(user_route), 0.04)

    def test_thirty_minute_performance(self) -> None:
        """A 30-minute simulated incident completes without wall-clock waiting."""
        started = time.perf_counter()
        _make_run(self.root / "long", 42, 1800, True, "database-proxy", "crash")
        self.assertLess(time.perf_counter() - started, 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
