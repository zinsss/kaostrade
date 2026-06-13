from __future__ import annotations

import argparse
import unittest
import unittest.mock
from pathlib import Path

from app.tui.paper_monitor import last_processed_table, monitor_snapshot, positive_int


class PaperMonitorTests(unittest.TestCase):
    def test_positive_int_rejects_non_positive_values(self) -> None:
        self.assertEqual(positive_int("5"), 5)
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

    def test_last_processed_table_handles_empty_values(self) -> None:
        table = last_processed_table({})

        self.assertEqual(len(table.columns), 2)
        self.assertEqual(len(table.rows), 1)

    def test_monitor_snapshot_reads_state_and_latest_prices(self) -> None:
        state = {
            "cash_krw": 1_000_000.0,
            "positions": {"KRW-BTC": {"quantity": 0.1}},
            "trade_log": [],
            "last_processed_timestamp_by_market": {"KRW-BTC": "2026-01-01T00:00:00"},
            "realized_pnl_krw": 0.0,
        }
        with unittest.mock.patch("app.tui.paper_monitor.load_state", return_value=state) as load_state, \
             unittest.mock.patch("app.tui.paper_monitor.connect_read_only") as connect_read_only, \
             unittest.mock.patch("app.tui.paper_monitor.latest_candle_prices", return_value={"KRW-BTC": 123.0}) as prices:
            connect_read_only.return_value.__enter__.return_value = object()

            snapshot = monitor_snapshot("candidate_v1", Path("state.json"), "db.sqlite", 5)

        load_state.assert_called_once_with(Path("state.json"))
        prices.assert_called_once_with(connect_read_only.return_value.__enter__.return_value, ["KRW-BTC"])
        self.assertEqual(snapshot["profile"], "candidate_v1")
        self.assertEqual(snapshot["refresh_seconds"], 5)
        self.assertEqual(snapshot["state"], state)
        self.assertEqual(snapshot["latest_prices"], {"KRW-BTC": 123.0})
        self.assertEqual(snapshot["state_path"], "state.json")
        self.assertEqual(snapshot["db_path"], "db.sqlite")
        self.assertIn("refresh_timestamp", snapshot)


if __name__ == "__main__":
    unittest.main()
