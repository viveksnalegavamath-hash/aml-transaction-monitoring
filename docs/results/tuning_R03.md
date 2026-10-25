# Tuning R03 — tuning months only (full data)

Baseline row (plan values) in bold. Δ alerts and accounts lost are against the baseline. Scored like the rules report: precision = alerts whose account had a suspicious transaction in the detection window, lift = precision / base rate, account recall = suspicious accounts alerted.

| variant | min_receivers | min_total_gbp | Alerts/week | Δ alerts | Precision | Lift | Account recall | Accounts lost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **plan** | **10** | **0** | **1,784** | **+0%** | **2.0%** | **2.5×** | **32.2%** | **0** |
| plan | 8 | 0 | 4,122 | +131% | 1.8% | 2.3× | 41.0% | 0 |
| plan | 8 | 10000 | 4,106 | +130% | 1.8% | 2.3× | 41.0% | 0 |
| plan | 8 | 20000 | 3,932 | +120% | 1.8% | 2.3× | 40.8% | 0 |
| plan | 8 | 50000 | 2,539 | +42% | 1.8% | 2.3× | 38.1% | 32 |
| plan | 10 | 10000 | 1,783 | -0% | 2.0% | 2.5× | 32.2% | 0 |
| plan | 10 | 20000 | 1,760 | -1% | 2.0% | 2.5× | 32.2% | 1 |
| plan | 10 | 50000 | 1,358 | -24% | 2.0% | 2.6× | 30.1% | 124 |
| plan | 8 | 100000 | 1,100 | -38% | 1.5% | 2.0× | 30.3% | 317 |
| plan | 10 | 100000 | 738 | -59% | 1.7% | 2.2× | 23.1% | 520 |
| plan | 15 | 0 | 328 | -82% | 1.2% | 1.6× | 10.5% | 1,248 |
| plan | 15 | 10000 | 328 | -82% | 1.2% | 1.6× | 10.5% | 1,248 |
| plan | 15 | 20000 | 328 | -82% | 1.2% | 1.6× | 10.5% | 1,248 |
| plan | 15 | 50000 | 326 | -82% | 1.2% | 1.6× | 10.3% | 1,256 |
| plan | 15 | 100000 | 316 | -82% | 1.1% | 1.4× | 9.8% | 1,288 |
| plan | 20 | 0 | 169 | -91% | 0.3% | 0.4× | 2.2% | 1,723 |
| plan | 20 | 10000 | 169 | -91% | 0.3% | 0.4× | 2.2% | 1,723 |
| plan | 20 | 20000 | 169 | -91% | 0.3% | 0.4× | 2.2% | 1,723 |
| plan | 20 | 50000 | 169 | -91% | 0.3% | 0.4× | 2.2% | 1,723 |
| plan | 20 | 100000 | 169 | -91% | 0.3% | 0.4× | 2.2% | 1,723 |
