# ML evaluation: rules vs ML vs rules + ML

Dataset: `features` · generated 2026-10-29 11:29 IST · labels joined only in evaluation code · scored once, no changes after.

- **Weeks:** holdout, 2023-06-01 to 2023-08-23 (12 weeks).
- **Base rate:** 0.51% (2,490 suspicious of 486,685 active accounts).
- **Same alert volume:** each model raises the top 5,053 account-weeks of every week, the tuned rules' average weekly alerts in these weeks. One rule alert = one alert, so the rules review fewer distinct accounts: 4,169 account-weeks a week. The sensitivity table holds the models to that number.
- Models chosen on May 2023 before this run: ML only = XGBoost (MLflow run `1588d9ee`), Rules + ML = XGBoost (MLflow run `58395689`).
- Precision, lift and account recall as in the rules report (alerts scored by account and time; ML alerts are raised at the end of their week). PR-AUC at account-week level; for the rules, the score is the number of rules alerting that week. Never accuracy.

## Same weekly alert volume

| Approach | Alerts / week | Precision | Lift | Account recall | Accounts caught | PR-AUC | True alerts / week |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Rules only (tuned) | 5,053 | 3.4% | 6.7× | 58.6% | 1,460 / 2,490 | 0.0072 | 172 | 4,169 distinct account-weeks/week |
| ML only, XGBoost | 5,053 | 9.7% | 18.9× | 99.8% | 2,486 / 2,490 | 0.7699 | 488 | selected |
| Rules + ML, XGBoost | 5,053 | 9.7% | 19.0× | 99.8% | 2,486 / 2,490 | 0.7712 | 492 | selected |
| ML only, logistic regression | 5,053 | 7.2% | 14.1× | 97.0% | 2,416 / 2,490 | 0.0775 | 365 | baseline |
| Rules + ML, logistic regression | 5,053 | 7.3% | 14.2× | 97.1% | 2,418 / 2,490 | 0.0773 | 367 | baseline |
| Rules + ML + artefact (diagnostic), XGBoost | 5,053 | 10.4% | 20.4× | 100.0% | 2,490 / 2,490 | 0.8002 | 527 | diagnostic |

## Sensitivity: models at the rules' distinct account-weeks (4,169 a week)

| Approach | Alerts / week | Precision | Lift | Account recall | Accounts caught | PR-AUC | True alerts / week |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Rules only (tuned) | 5,053 | 3.4% | 6.7× | 58.6% | 1,460 / 2,490 | 0.0072 | 172 | 4,169 distinct account-weeks/week |
| ML only at 4,169/week | 4,169 | 11.1% | 21.6× | 99.7% | 2,483 / 2,490 | 0.7699 | 461 |  |
| Rules + ML at 4,169/week | 4,169 | 11.1% | 21.8× | 99.8% | 2,485 / 2,490 | 0.7712 | 464 |  |

## Fewer alerts: recall at smaller weekly volumes

Decided before the hold-out was opened, because validation showed the models catching almost every suspicious account at the rules' volume: the same models at smaller weekly volumes (top alerts of each week).

| Alerts / week | ML only: account recall | ML only: precision | ML only: lift | Rules + ML: account recall | Rules + ML: precision | Rules + ML: lift |
| --- | --- | --- | --- | --- | --- | --- |
| 250 | 67.5% | 73.4% | 143.5× | 67.4% | 73.3% | 143.2× |
| 500 | 88.8% | 49.6% | 97.0× | 89.2% | 49.6% | 96.9× |
| 1,000 | 97.0% | 29.9% | 58.5× | 96.7% | 29.6% | 57.8× |
| 2,500 | 99.5% | 15.6% | 30.5× | 99.5% | 15.6% | 30.4× |

Smallest weekly volume that catches at least as many suspicious accounts as the rules (1,460): ML only: **199 a week** (96% fewer alerts than the rules' 5,053); Rules + ML: **197 a week** (96% fewer alerts than the rules' 5,053).

## Suspicious accounts: rules vs rules + ML

| Suspicious accounts | Count |
| --- | --- |
| Caught by both | 1,459 |
| Rules only | 1 |
| Rules + ML only | 1,027 |
| Neither | 3 |

## Account recall by typology

| Typology | Suspicious accounts | Rules only (tuned) | ML only, XGBoost | Rules + ML, XGBoost |
| --- | --- | --- | --- | --- |
| behavioural_change_1 | 144 | 12.5% | 97.9% | 97.9% |
| behavioural_change_2 | 147 | 15.6% | 100.0% | 100.0% |
| bipartite | 41 | 70.7% | 100.0% | 100.0% |
| cash_withdrawal | 100 | 63.0% | 99.0% | 99.0% |
| cycle | 86 | 84.9% | 100.0% | 100.0% |
| deposit_send | 326 | 64.7% | 100.0% | 100.0% |
| fan_in | 95 | 63.2% | 100.0% | 100.0% |
| fan_out | 141 | 55.3% | 100.0% | 100.0% |
| gather_scatter | 137 | 58.4% | 100.0% | 100.0% |
| layered_fan_in | 154 | 67.5% | 100.0% | 100.0% |
| layered_fan_out | 150 | 58.7% | 100.0% | 100.0% |
| over_invoicing | 22 | 90.9% | 100.0% | 100.0% |
| scatter_gather | 65 | 63.1% | 100.0% | 100.0% |
| single_large | 88 | 70.5% | 100.0% | 100.0% |
| smurfing | 68 | 79.4% | 100.0% | 100.0% |
| stacked_bipartite | 124 | 66.1% | 100.0% | 100.0% |
| structuring | 602 | 62.1% | 100.0% | 100.0% |

## Example alerts with their top three reasons (synthetic accounts)

**ML only**

- account 5826952585, week ending 2023-08-23, score 1.000: 4 new payees in 7 days; 3 new senders in 7 days; 7 new senders in 30 days
- account 3213443371, week ending 2023-08-23, score 1.000: 5 new senders in 7 days; 10 new senders in 30 days; 5 payments received in 7 days
- account 8878987296, week ending 2023-08-23, score 1.000: 2 new senders in 7 days; 7 new senders in 30 days; 0 new payees in 90 days

**Rules + ML**

- account 5826952585, week ending 2023-08-23, score 1.000: 4 new payees in 7 days; 7 new senders in 30 days; 3 new senders in 7 days
- account 3213443371, week ending 2023-08-23, score 1.000: 5 new senders in 7 days; 10 new senders in 30 days; 5 payments received in 7 days
- account 6128626942, week ending 2023-08-23, score 1.000: 4 payments received in 7 days; 10 payments received in 30 days; 2 different payees in 90 days
