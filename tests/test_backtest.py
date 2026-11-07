import unittest
import pandas as pd

from xau_intel.backtest import BacktestConfig, _simulate_stream, summarize


class BacktestSafetyTests(unittest.TestCase):
    def test_same_bar_stop_and_target_uses_stop(self):
        frame = pd.DataFrame([
            {"time": "2026-01-01 00:00", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": True},
            {"time": "2026-01-01 00:01", "open": 100.0, "high": 102.0,
             "low": 98.0, "close": 100.0, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
            {"time": "2026-01-01 00:02", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
        ])
        trades = _simulate_stream(frame, "signal", 1, BacktestConfig())
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "stop")
        self.assertLess(trades[0]["pnl_price"], 0)

    def test_empty_summary_is_safe(self):
        result = summarize(pd.DataFrame())
        self.assertEqual(result["trades"], 0)
        self.assertIsNone(result["profit_factor"])

    def test_config_rejects_negative_costs(self):
        with self.assertRaises(ValueError):
            BacktestConfig(slippage_per_side=-0.01).validate()


if __name__ == "__main__":
    unittest.main()
