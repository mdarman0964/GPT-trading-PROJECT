# Architecture and implementation boundary

```text
CSV / Broker Market Data
        |
        v
Data Validation -> Feature Builder -> Research / Hypothesis Engine
                                      |
                                      v
                             Backtest + Cost Model
                                      |
                                      v
                         Out-of-sample Evaluation
                                      |
                                      v
                          Demo Forward Validation
                                      |
                                      v
                    Risk Gate -> Broker Order Adapter (not yet built)
```

The research engine must test liquidity sweeps, false breakouts, session effects, volatility regimes, spread filters and long/short asymmetry as separate measurable hypotheses before combining them. Avoid look-ahead bias, use chronological splits, and model execution costs.

Live execution is deliberately excluded until the strategy, contract specifications, API signing requirements, risk limits and demo forward test are verified.
