import unittest
import pandas as pd

from xau_intel.backtest import BacktestConfig, _simulate_stream, summarize


def frame_for_signal(rows):
    return pd.DataFrame(rows)


class BacktestSafetyTests(unittest.TestCase):
    def test_same_bar_stop_and_target_uses_stop(self):
        frame = frame_for_signal([
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

    def test_long_trailing_stop_locks_profit_after_activation(self):
        frame = frame_for_signal([
            {"time": "2026-01-01 00:00", "open": 100.0, "high": 100.1,
             "low": 99.9, "close": 100.0, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": True},
            {"time": "2026-01-01 00:01", "open": 100.0, "high": 100.5,
             "low": 99.8, "close": 100.4, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
            {"time": "2026-01-01 00:02", "open": 100.4, "high": 101.0,
             "low": 100.2, "close": 100.9, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
            {"time": "2026-01-01 00:03", "open": 100.4, "high": 100.6,
             "low": 100.1, "close": 100.3, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
        ])
        trades = _simulate_stream(frame, "signal", 1, BacktestConfig())
        self.assertEqual(len(trades), 1)
        self.assertTrue(trades[0]["trailing_activated"])
        self.assertEqual(trades[0]["exit_reason"], "trailing_stop_gap")
        self.assertGreater(trades[0]["pnl_price"], 0)

    def test_disabled_trailing_preserves_original_stop_path(self):
        frame = frame_for_signal([
            {"time": "2026-01-01 00:00", "open": 100.0, "high": 100.1,
             "low": 99.9, "close": 100.0, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": True},
            {"time": "2026-01-01 00:01", "open": 100.0, "high": 101.0,
             "low": 99.8, "close": 100.8, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
            {"time": "2026-01-01 00:02", "open": 100.8, "high": 100.9,
             "low": 98.9, "close": 99.0, "spread": 0.2, "atr": 1.0,
             "spread_ok": True, "spread_to_atr": 0.2, "signal": False},
        ])
        cfg = BacktestConfig(trailing_activation_fraction=None)
        trades = _simulate_stream(frame, "signal", 1, cfg)
        self.assertEqual(trades[0]["exit_reason"], "stop")
        self.assertFalse(trades[0]["trailing_activated"])

    def test_empty_summary_is_safe(self):
        result = summarize(pd.DataFrame())
        self.assertEqual(result["trades"], 0)
        self.assertIsNone(result["profit_factor"])

    def test_config_rejects_negative_costs(self):
        with self.assertRaises(ValueError):
            BacktestConfig(slippage_per_side=-0.01).validate()

    def test_config_rejects_invalid_trailing_fraction(self):
        with self.assertRaises(ValueError):
            BacktestConfig(trailing_activation_fraction=1.0).validate()


if __name__ == "__main__":
    unittest.main()
