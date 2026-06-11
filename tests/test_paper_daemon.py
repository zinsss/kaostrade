from __future__ import annotations

import argparse
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

from app.paper.daemon import positive_int, run_daemon, summary_line


def result_summary(actions: list[dict] | None = None) -> dict:
    return {
        "summary": {
            "cash": 1_000_000.0,
            "equity": 1_001_234.5,
            "realized_pnl": 100.25,
            "unrealized_pnl": 1_134.25,
            "open_positions": 2,
            "trade_count": 7,
        },
        "actions": actions if actions is not None else [{"side": "BUY"}, {"side": "SELL"}],
    }


class PaperDaemonTests(unittest.TestCase):
    def test_summary_line_contains_requested_fields(self) -> None:
        line = summary_line(
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            "candidate_v1",
            result_summary(),
        )

        self.assertIn("timestamp=2026-01-01T00:00:00+00:00", line)
        self.assertIn("profile=candidate_v1", line)
        self.assertIn("cash=1000000.00", line)
        self.assertIn("equity=1001234.50", line)
        self.assertIn("realized_pnl=100.25", line)
        self.assertIn("unrealized_pnl=1134.25", line)
        self.assertIn("open_positions=2", line)
        self.assertIn("trade_count=7", line)
        self.assertIn("new_action_count=2", line)

    def test_positive_int_rejects_non_positive_values(self) -> None:
        self.assertEqual(positive_int("60"), 60)
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

    def test_run_daemon_once_runs_without_sleeping(self) -> None:
        with unittest.mock.patch("app.paper.daemon.load_config", return_value={"database": {"path": "db.sqlite"}}), \
             unittest.mock.patch("app.paper.daemon.run_once", return_value=result_summary(actions=[])) as run_once, \
             unittest.mock.patch("app.paper.daemon.time.sleep") as sleep, \
             unittest.mock.patch("builtins.print") as print_mock:
            run_daemon("candidate_v1", 60, Path("state.json"), once=True)

        run_once.assert_called_once_with(profile_name="candidate_v1", db_path="db.sqlite", state_path=Path("state.json"))
        sleep.assert_not_called()
        self.assertIn("new_action_count=0", print_mock.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
