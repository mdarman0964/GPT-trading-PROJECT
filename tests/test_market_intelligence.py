import unittest
import pandas as pd
from xau_intel.market_intelligence import IntelligenceConfig, build_market_intelligence, validate_market_data

def sample(times=None):
    times = times if times is not None else pd.date_range("2026-01-01", periods=40, freq="min")
    return pd.DataFrame([{"time":t,"open":2000+i*.01,"high":2000+i*.01+.4,"low":2000+i*.01-.4,"close":2000+i*.01+.1,"volume":10,"spread":.2} for i,t in enumerate(times)])

class MarketIntelligenceTests(unittest.TestCase):
    def test_gap_starts_new_segment(self):
        times=list(pd.date_range("2026-01-01",periods=3,freq="min"))+list(pd.date_range("2026-01-01 00:10",periods=3,freq="min"))
        out=validate_market_data(sample(times)); self.assertEqual(out.segment_id.nunique(),2); self.assertTrue(bool(out.loc[3,"gap_before"]))
    def test_invalid_ohlc_rejected(self):
        df=sample(); df.loc[4,"high"]=df.loc[4,"low"]-1
        with self.assertRaises(ValueError): validate_market_data(df)
    def test_pivot_only_visible_after_right_bars(self):
        df=sample(); df.loc[10,"high"]=2010; df.loc[11,"high"]=2002; df.loc[12,"high"]=2001
        out=build_market_intelligence(df,IntelligenceConfig(swing_left=2,swing_right=2,liquidity_lookback=10))
        self.assertTrue(pd.isna(out.loc[11,"confirmed_swing_high_event"])); self.assertEqual(out.loc[12,"confirmed_swing_high_event"],2010)
    def test_fvg_does_not_cross_gap(self):
        times=list(pd.date_range("2026-01-01",periods=3,freq="min"))+list(pd.date_range("2026-01-01 00:10",periods=3,freq="min"))
        df=sample(times); df.loc[2,"high"]=2100; df.loc[3,"low"]=2200
        out=build_market_intelligence(df,IntelligenceConfig(swing_left=1,swing_right=1,liquidity_lookback=5))
        self.assertFalse(bool(out.loc[3,"bullish_fvg"]))
    def test_negative_volume_rejected(self):
        df=sample(); df.loc[0,"volume"]=-1
        with self.assertRaises(ValueError): validate_market_data(df)
if __name__=="__main__": unittest.main()
