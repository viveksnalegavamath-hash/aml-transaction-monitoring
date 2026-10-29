# Model documentation: weekly account risk ranking (XGBoost)

> **Synthetic data only.** Transactions come from SAML-D, a synthetic dataset; no real person or account is involved. The numbers below describe how the method performs on that dataset. They are **not** an estimate of performance on real bank data, which would be much lower (section 5).

| | |
| --- | --- |
| Model | XGBoost classifier, in two variants: **ML only** (56 behaviour features) and **rules + ML** (the same 56 features plus 9 rule-hit features) |
| Version | Trained 29 Oct 2026 · MLflow experiment `aml-account-week`, runs `1588d9ee` (ML only) and `58395689` (rules + ML) |
| Owner | Vivek Nalegavamath (portfolio project) |
| Status | Development. Not deployed. Hold-out alerts with reasons are in `marts.ml_alerts` |
| Code and settings | `dbt/models/features/`, `src/aml_transaction_monitoring/ml/`, `src/aml_transaction_monitoring/evaluation/ml_experiment.py`, `config/ml.yaml` |
| Results | [`results/ml_validation.md`](results/ml_validation.md) (model choice) · [`results/ml_evaluation.md`](results/ml_evaluation.md) (hold-out, scored once) |

## 1. Purpose and scope

- **What it does:** once a week, at the end of the week (Wednesday), it scores every account that sent or received money that week. The score estimates how likely the week contains laundering. The highest-scoring account-weeks become alerts, up to a fixed weekly capacity.
- **Why:** the tuned rules raise about 5,053 alerts a week and catch 58.6% of suspicious accounts on the hold-out, below the 65% goal. The model is meant to rank activity so that a team of fixed size reviews the riskiest accounts first.
- **Decision support only.** An alert goes to an analyst (L1), who decides whether to escalate. The model never files a SAR, blocks a payment or closes an account. Every alert carries its three main reasons in plain English.
- **Out of scope:** real-time payment scoring, customer risk rating, sanctions screening (S01 handles that).

## 2. Data

- **Source:** `marts.fct_transactions` (SAML-D, 9.5M transactions, built and tested in dbt). The feature table `features.feat_account_week` has one row per **active account-week**: an account with at least one transaction sent or received in a Thursday-to-Wednesday week. That gives 5,210,906 account-weeks over 46 weeks.
- **Label:** the account sent or received a transaction labelled as laundering that week (`evaluation.eval_account_week_labels`). The label lives in the `evaluation` schema. Only the evaluation code joins it, after the features exist; tests check that no feature model or ML module reads it.
- **Time split.** A week belongs to the month of its last day, the day it is scored. Nothing is split at random.

| Part | Weeks scored | Account-weeks | Positive | Rate | Suspicious accounts |
| --- | --- | --- | --- | --- | --- |
| Train | 4 Jan – 26 Apr 2023 (17) | 1,950,059 | 4,428 | 0.23% | 3,168 |
| Validation | 3 – 31 May 2023 (5) | 569,962 | 1,112 | 0.20% | 874 |
| Hold-out | 7 Jun – 23 Aug 2023 (12) | 1,370,227 | 3,269 | 0.24% | 2,490 |

- October–December 2022 is not used: the data starts on 7 Oct 2022, so 90-day windows there would be cut short. The hold-out months are the same as the rules' hold-out, and they were scored once, after the models had been chosen on May.

## 3. Method

**Features** (SQL in dbt; every window ends on the scoring Wednesday):

| Group | Features | Count |
| --- | --- | --- |
| Money in and out, per 7, 30 and 90 days | number of payments, total, largest payment, distinct counterparties, **new** counterparties (never paid or paid by before), share of the total that was cash, cross-border, or to or from a high-risk country (internal rating high or FATF-listed) | 48 |
| Flow balance | share of all money moved that went out (7, 30, 90 days) | 3 |
| Own history | this week's total and largest payment against the account's usual week and usual payment (days 8–90), as log ratios; days since first seen | 5 |
| Rule hits (rules + ML only) | alerts from each enabled rule in the week; distinct rules alerting in 30 days | 9 |

- **Point in time.** Features use only transactions dated on or before the end of their week. A dbt test recomputes every windowed feature by brute force for a fixed 1-in-256 subset of accounts and compares the results. It was checked to fail when one window was made to look a single day ahead. Rule hits count only alerts dated on or before the week's end (pytest).
- **Left out on purpose:**
  - The SAML-D "receiver is a customer" flag. It is a quirk of how SAML-D generates laundering (true for 92.5% of suspicious vs 33.2% of normal transactions), and the customer list covers the whole period, future included. It is measured separately below.
  - The synthetic KYC attributes, which carry no signal by design.
- **Models.**
  - Logistic regression baseline: inputs transformed as sign(x)·ln(1 + |x|), then standardised, with classes weighted to balance.
  - XGBoost with class weights (`scale_pos_weight` = negatives ÷ positives, or its square root), learning rate 0.05, early stopping on May (PR-AUC, 100 rounds), grid of depth 4 or 6 × the two weightings, seed 20261027.
  - For each feature set, the model with the highest validation PR-AUC was kept. Negatives were not down-sampled: training needed about 3.4 GB of memory at peak.
- **Alerts:** the top *k* account-weeks of every week, the same capacity each week. In the headline comparison *k* = 5,053, the rules' average weekly alerts in the hold-out.
- **Reasons:** exact TreeSHAP values. The three features that pushed an alert's score up most are turned into sentences from templates in `config/ml.yaml`, e.g. *"4 new payees in 7 days; 3 new senders in 7 days; 7 new senders in 30 days"*. Each reason is stored with its value and SHAP value in `marts.ml_alerts`.
- **Tracking:** every run (parameters, metrics, feature list) is in MLflow under `mlruns/` (local, git-ignored). Trained models are in `data/ml/` (git-ignored).

## 4. Performance

**Model choice on May 2023** (validation; full table in `results/ml_validation.md`):

| Model | PR-AUC | Account recall at the rules' 5,064 alerts/week |
| --- | --- | --- |
| Rules only (tuned) | – | 43.1% |
| Logistic regression, ML only | 0.059 | 95.7% |
| **XGBoost, ML only** (depth 6, square-root weight) | **0.710** | 99.8% |
| **XGBoost, rules + ML** (depth 6, square-root weight) | **0.714** | 99.8% |

**Hold-out, scored once** (1 Jun – 23 Aug 2023; base rate 0.51% of 486,685 active accounts):

| Approach | Alerts / week | Precision | Lift | Account recall | PR-AUC |
| --- | --- | --- | --- | --- | --- |
| Rules only (tuned) | 5,053 | 3.4% | 6.7× | 58.6% | 0.007 |
| ML only, XGBoost | 5,053 | 9.7% | 18.9× | 99.8% | 0.770 |
| **Rules + ML, XGBoost** | 5,053 | 9.7% | 19.0× | **99.8%** | 0.771 |
| Rules + ML, logistic regression (baseline) | 5,053 | 7.3% | 14.2× | 97.1% | 0.077 |
| Rules + ML, XGBoost, at 250 alerts/week | 250 | 73.3% | 143× | 67.4% | 0.771 |

- **Same volume:** rules + ML catches 2,486 of 2,490 suspicious accounts against the rules' 1,460. It catches **1,027 accounts the rules missed** and misses only one account the rules caught. True alerts go up from 172 to 492 a week.
- **Same recall, fewer alerts:** to catch as many suspicious accounts as the rules, the model needs **197 alerts a week, 96% fewer** than the rules' 5,053.
- **Fair capacity check:** the rules' 5,053 alerts cover 4,169 distinct account-weeks a week, because some accounts get several rule alerts. At 4,169 a week the model's recall is still 99.8%.
- **By typology:** the rules are weakest on behavioural change (12.5% and 15.6% of accounts); the model catches 97.9% and 100%.
- **Rule hits add almost nothing** on top of the behaviour features (PR-AUC 0.771 vs 0.770). The rules' value here is as documented, explainable controls, not extra signal.
- **The artefact would flatter the model:** adding the "receiver is a customer" columns raises hold-out PR-AUC from 0.771 to 0.800, and recall to 100%. That is why they are excluded.
- Precision is capped by capacity: there are only about 270 positive account-weeks a week, so 5,053 alerts must be mostly negative. The smaller volumes show the precision the ranking can reach.

## 5. Limitations and risks

1. **Synthetic data makes detection unrealistically easy.**
   - In SAML-D, 90.9% of positive account-weeks involve a counterparty never seen before, against 11.5% of normal ones. Counterparty features dominate the reasons: in the hold-out alerts, distinct senders over 90 days is among the top three reasons for 41% of alerts, and new senders or new payees in 7 days for 28–29% each.
   - A typical suspicious account has 179 normal transactions and one laundering transaction, so the model finds the week of that transaction rather than a "bad customer".
   - Real launderers reuse accounts and counterparties, and real normal customers also pay new people. Expect far lower precision and recall on real data. The method — features, time split, capacity-based comparison, explanations — is what transfers.
2. **Labels.** SAML-D labels are complete and correct by construction. Real labels (closed alerts, SARs, law-enforcement feedback) are incomplete and shaped by what past controls looked at, so a model trained on them inherits those blind spots.
3. **Rule-hit features come from rules tuned on January–May,** which includes the validation month. That flatters rules + ML's validation slightly; the hold-out is unaffected.
4. **One time split,** with 17 training weeks and no rolling re-validation. Drift over longer periods is untested.
5. **History starts on 7 Oct 2022.** Days since first seen and new counterparties treat anything before that as unknown.
6. **Explanations describe the model, not causes.** Some reasons are absence signals, such as "0 new payees in 90 days"; they mean the account looks unusual compared with a typical one, not that the value itself is suspicious. Analysts must review the whole account, as in the worked case.
7. **No personal attributes are used.** All features describe transaction behaviour, so the model cannot treat customers differently by nationality, occupation or other KYC data. On real data, a fairness review would still be needed, because behaviour can act as a stand-in for personal attributes.

## 6. Monitoring and governance (if deployed)

- **Weekly:** alert volume against capacity, score distribution, share of alerts per top reason.
- **Monthly:** population stability index (PSI) of the score and the top features; review the model if any PSI is above 0.25. Also precision from closed alerts.
- **Quarterly:** back-test recall against SARs and confirmed cases, and run the model as a challenger to the rules (champion–challenger) before it changes any rule.
- **Change control:** retrain only through `config/ml.yaml` with a new MLflow run, on a later time split, then evaluate once on unseen weeks. Record every change and its evidence in the change log. The hold-out stage refuses to run twice, so a hold-out cannot quietly become a second validation set.
- **Human in the loop:** the model ranks; analysts decide; the MLRO decides on SARs.

## 7. How to reproduce

```bash
cd dbt && dbt build --select feat_account_week+ eval_account_week_labels+ && cd ..
uv run python -m aml_transaction_monitoring.rules.engine
uv run python -m aml_transaction_monitoring.evaluation.ml_experiment validate
uv run python -m aml_transaction_monitoring.evaluation.ml_experiment holdout   # once
```

Development runs use `--sample`. They print their reports and never open the hold-out: the sample's holdout stage is a dry run on the May weeks.
