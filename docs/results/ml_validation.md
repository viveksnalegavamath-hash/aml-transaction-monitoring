# ML validation (model selection)

Dataset: `features` · generated 2026-10-29 10:32 IST · labels joined only in evaluation code.

- **Train:** weeks scored 2023-01-04 to 2023-04-26 (1,950,059 account-weeks, 4,428 positive).
- **Validation (May 2023):** weeks scored 2023-05-03 to 2023-05-31 (569,962 account-weeks, 1,112 positive; 874 suspicious accounts, base rate 0.26%).
- The hold-out (Jun – Aug 2023) is not read here.
- Each model's alerts: the top 5,064 account-weeks of each week, the rules' own weekly volume in these weeks. Rules alone at that volume: account recall 43.1%, precision 3.2%, lift 12.1×.
- **PR-AUC** is at account-week level (label: a suspicious transaction that week); selection = highest PR-AUC per feature set. Precision, lift and account recall use the rules report's definitions. Never accuracy.

| Feature set | Model | Settings | Trees | PR-AUC | Account recall | Precision | Lift |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ML only | logistic regression | – | – | 0.0589 | 95.7% | 6.1% | 23.1× | kept (best of family) |
| ML only | XGBoost | max_depth=4, scale_pos_weight=balanced | 1458 | 0.6326 | 99.8% | 8.4% | 32.2× |  |
| ML only | XGBoost | max_depth=4, scale_pos_weight=sqrt | 1465 | 0.6943 | 99.9% | 8.8% | 33.6× |  |
| ML only | XGBoost | max_depth=6, scale_pos_weight=balanced | 935 | 0.6386 | 99.4% | 8.8% | 33.4× |  |
| ML only | XGBoost | max_depth=6, scale_pos_weight=sqrt | 1530 | 0.7095 | 99.8% | 8.8% | 33.4× | **selected** |
| Rules + ML | logistic regression | – | – | 0.0591 | 95.3% | 6.1% | 23.3× | kept (best of family) |
| Rules + ML | XGBoost | max_depth=4, scale_pos_weight=balanced | 2315 | 0.6437 | 99.8% | 8.6% | 32.9× |  |
| Rules + ML | XGBoost | max_depth=4, scale_pos_weight=sqrt | 1384 | 0.6942 | 99.8% | 8.8% | 33.4× |  |
| Rules + ML | XGBoost | max_depth=6, scale_pos_weight=balanced | 1392 | 0.6479 | 99.4% | 8.7% | 33.0× |  |
| Rules + ML | XGBoost | max_depth=6, scale_pos_weight=sqrt | 1178 | 0.7142 | 99.8% | 8.7% | 33.3× | **selected** |
| Rules + ML + artefact (diagnostic) | XGBoost | max_depth=6, scale_pos_weight=sqrt | 1016 | 0.7361 | 99.9% | 9.5% | 36.3× | diagnostic |

Artefact diagnostic: adding the SAML-D "receiver is a customer" columns to the selected rules + ML model changes validation PR-AUC from 0.7142 to 0.7361. It is never a candidate (dataset artefact, and the customer list covers the future).
