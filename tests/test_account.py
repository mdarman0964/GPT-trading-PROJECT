import unittest
import pandas as pd

from xau_intel.account import AccountConfig, run_account_backtest


class AccountBacktestTests(unittest.TestCase):
    def sample(self):
        return pd.DataFrame([{
            "entry_time": "2026-01-01 00:01", "exit_time": "2026-01-01 00:03",
            "entry_index": 1, "exit_index": 3, "entry_mid": 2000.0,
            "pnl_price": 1.0, "stop_distance": 2.0, "risk_distance": 2.2,
            "entry_spread": 0.2, "exit_spread": 0.2, "hypothesis": "sample",
        }])

    def test_sizes_position_from_one_percent_risk(self):
        taken, equity, report = run_account_backtest(
            self.sample(), AccountConfig(contract_size=100, leverage=100))
        self.assertEqual(len(taken), 1)
        self.assertAlmostEqual(taken.iloc[0]["risk_budget_usd"], 10.0)
        self.assertAlmostEqual(taken.iloc[0]["lots"], 0.04)
        self.assertAlmostEqual(report["ending_balance_usd"], 1004.0)

    def test_overlapping_trades_are_not_double_counted(self):
        trades = pd.concat([self.sample(), self.sample()], ignore_index=True)
        taken, _, report = run_account_backtest(
            trades, AccountConfig(contract_size=100, leverage=100))
        self.assertEqual(len(taken), 1)
        self.assertEqual(report["skipped_overlapping"], 1)

    def test_invalid_risk_rejected(self):
        with self.assertRaises(ValueError):
            AccountConfig(risk_fraction=0).validate()


if __name__ == "__main__":
    unittest.main()
