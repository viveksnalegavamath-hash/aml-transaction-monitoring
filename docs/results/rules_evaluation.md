# Rules evaluation — TUNED (R03, R06, R07 version 2; hold-out run once, 25 Oct 2026)

Dataset: `marts` · generated 2026-10-25 13:36 IST · labels joined only after alerts exist.

Rule versions: R01 v1 (run 2), R02 v1 (run 3), R03 v2 (run 10), R04 v1 (run 5), R05 v1 (run 6), R06 v2 (run 11), R07 v2 (run 12), R09 v1 (run 9)

- **Hold-out (headline):** 2023-06-01 to 2023-08-23
- **Tuning:** 2022-10-07 to 2023-05-31

Every rule is scored the same way, by the alerted account and the alert time, not by the transactions a rule links to its alerts. A suspicious account is the sender or receiver of a suspicious transaction.

- **Precision** = share of alerts whose account had a suspicious transaction in the 30 days up to the alert.
- **Base rate** = share of active accounts in the period that are suspicious: the precision of picking accounts at random. **Lift** = precision / base rate. The base rate looks at the whole period, precision at 30 days, so lift is slightly understated (conservative).
- **Account recall** (headline) = share of suspicious accounts with an alert in the period.
- **Transaction recall** = share of suspicious transactions whose sender or receiver was alerted at the time or up to 30 days after.
- Never accuracy.

## Hold-out (headline)

Base rate: 0.51% (2,490 suspicious of 486,685 active accounts).

| Rule | Name | Alerts | Alerts / week | Precision | Lift | Account recall | Accounts caught | Transaction recall | Transactions caught |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | Structuring | 0 | 0.0 | – | – | 0.0% | 0 / 2,490 | 0.0% | 0 / 2,837 |
| R02 | Many-to-one | 4,259 | 354.9 | 3.9% | 7.6× | 6.8% | 170 / 2,490 | 12.3% | 350 / 2,837 |
| R03 | One-to-many | 8,468 | 705.7 | 2.1% | 4.0× | 10.5% | 261 / 2,490 | 13.9% | 395 / 2,837 |
| R04 | Rapid pass-through | 10,817 | 901.4 | 3.0% | 5.9× | 10.4% | 260 / 2,490 | 20.3% | 575 / 2,837 |
| R05 | High-risk jurisdiction | 8,865 | 738.8 | 2.7% | 5.2× | 11.4% | 283 / 2,490 | 14.7% | 416 / 2,837 |
| R06 | Large vs profile | 9,926 | 827.2 | 3.6% | 7.0× | 26.6% | 663 / 2,490 | 20.4% | 580 / 2,837 |
| R07 | Behaviour change | 9,542 | 795.2 | 5.4% | 10.6× | 21.4% | 532 / 2,490 | 21.5% | 609 / 2,837 |
| R09 | Cash intensity | 8,763 | 730.2 | 3.3% | 6.5× | 16.5% | 412 / 2,490 | 17.3% | 492 / 2,837 |
| any rule |  | – | – | – | – | 58.6% | 1,460 / 2,490 | 65.9% | 1,871 / 2,837 |

### Rule × typology (transaction recall), hold-out (headline)

| Typology | R01 | R02 | R03 | R04 | R05 | R06 | R07 | R09 | any rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| behavioural_change_1 | 0.0% | 82.5% | 70.6% | 96.0% | 17.5% | 0.0% | 0.0% | 0.0% | 96.0% |
| behavioural_change_2 | 0.0% | 84.7% | 66.1% | 94.4% | 30.6% | 0.0% | 0.0% | 0.0% | 98.4% |
| bipartite | 0.0% | 3.6% | 0.0% | 3.6% | 45.5% | 9.1% | 32.7% | 7.3% | 67.3% |
| cash_withdrawal | 0.0% | 8.8% | 7.5% | 5.0% | 2.2% | 21.2% | 21.2% | 17.0% | 47.2% |
| cycle | 0.0% | 2.5% | 9.9% | 72.8% | 18.5% | 24.7% | 24.7% | 1.2% | 88.9% |
| deposit_send | 0.0% | 3.1% | 12.5% | 26.6% | 22.7% | 19.5% | 16.8% | 55.5% | 77.7% |
| fan_in | 0.0% | 13.1% | 2.4% | 1.2% | 38.1% | 36.9% | 39.3% | 4.8% | 83.3% |
| fan_out | 0.0% | 0.0% | 8.9% | 6.5% | 22.8% | 8.1% | 26.0% | 6.5% | 63.4% |
| gather_scatter | 0.0% | 3.3% | 7.3% | 13.8% | 18.7% | 6.5% | 29.3% | 5.7% | 54.5% |
| layered_fan_in | 0.0% | 0.6% | 12.4% | 16.5% | 14.7% | 43.5% | 31.2% | 5.9% | 68.8% |
| layered_fan_out | 0.0% | 2.3% | 6.2% | 0.8% | 6.2% | 15.4% | 23.1% | 7.7% | 41.5% |
| over_invoicing | 0.0% | 0.0% | 14.3% | 14.3% | 0.0% | 85.7% | 92.9% | 0.0% | 100.0% |
| scatter_gather | 0.0% | 6.9% | 17.2% | 3.4% | 29.9% | 43.7% | 26.4% | 2.3% | 69.0% |
| single_large | 0.0% | 7.1% | 5.4% | 8.9% | 16.1% | 41.1% | 53.6% | 14.3% | 80.4% |
| smurfing | 0.0% | 5.7% | 9.1% | 13.7% | 0.0% | 24.0% | 16.7% | 65.8% | 80.2% |
| stacked_bipartite | 0.0% | 6.3% | 14.1% | 11.0% | 6.3% | 23.6% | 29.3% | 7.9% | 60.2% |
| structuring | 0.0% | 6.9% | 5.8% | 11.9% | 15.5% | 17.3% | 16.8% | 7.2% | 54.2% |

## Tuning period

Base rate: 0.78% (5,737 suspicious of 736,925 active accounts).

| Rule | Name | Alerts | Alerts / week | Precision | Lift | Account recall | Accounts caught | Transaction recall | Transactions caught |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | Structuring | 0 | 0.0 | – | – | 0.0% | 0 / 5,737 | 0.0% | 0 / 7,036 |
| R02 | Many-to-one | 12,129 | 358.2 | 2.9% | 3.7× | 16.3% | 936 / 5,737 | 12.2% | 857 / 7,036 |
| R03 | One-to-many | 24,979 | 737.8 | 1.7% | 2.2× | 23.1% | 1,328 / 5,737 | 13.6% | 955 / 7,036 |
| R04 | Rapid pass-through | 25,749 | 760.5 | 2.8% | 3.6× | 13.2% | 759 / 5,737 | 19.1% | 1,344 / 7,036 |
| R05 | High-risk jurisdiction | 24,565 | 725.5 | 2.1% | 2.8× | 15.5% | 887 / 5,737 | 16.7% | 1,178 / 7,036 |
| R06 | Large vs profile | 19,116 | 564.6 | 2.9% | 3.7× | 36.1% | 2,070 / 5,737 | 14.3% | 1,007 / 7,036 |
| R07 | Behaviour change | 13,280 | 392.2 | 4.8% | 6.2× | 24.5% | 1,408 / 5,737 | 13.3% | 939 / 7,036 |
| R09 | Cash intensity | 33,111 | 978.0 | 2.3% | 3.0× | 34.2% | 1,961 / 5,737 | 19.3% | 1,358 / 7,036 |
| any rule |  | – | – | – | – | 78.5% | 4,502 / 5,737 | 63.0% | 4,434 / 7,036 |

### Rule × typology (transaction recall), tuning period

| Typology | R01 | R02 | R03 | R04 | R05 | R06 | R07 | R09 | any rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| behavioural_change_1 | 0.0% | 92.2% | 79.5% | 100.0% | 10.1% | 0.0% | 0.0% | 0.0% | 100.0% |
| behavioural_change_2 | 0.0% | 94.1% | 70.1% | 100.0% | 42.1% | 0.0% | 0.0% | 0.0% | 100.0% |
| bipartite | 0.0% | 7.3% | 8.5% | 8.2% | 22.0% | 13.7% | 18.0% | 14.0% | 56.7% |
| cash_withdrawal | 0.0% | 4.3% | 8.7% | 4.3% | 6.3% | 10.7% | 11.9% | 16.6% | 47.5% |
| cycle | 0.0% | 6.3% | 9.6% | 69.8% | 17.6% | 28.9% | 20.6% | 2.7% | 85.4% |
| deposit_send | 0.0% | 4.6% | 8.3% | 28.2% | 26.0% | 11.8% | 14.5% | 59.5% | 79.2% |
| fan_in | 0.0% | 26.1% | 7.1% | 16.1% | 12.9% | 12.1% | 18.6% | 4.6% | 67.9% |
| fan_out | 0.0% | 0.0% | 22.8% | 9.6% | 50.0% | 4.4% | 9.6% | 7.0% | 71.1% |
| gather_scatter | 0.0% | 2.6% | 15.2% | 8.2% | 32.5% | 14.3% | 19.0% | 8.2% | 63.6% |
| layered_fan_in | 0.0% | 5.1% | 12.8% | 8.2% | 15.4% | 32.7% | 17.9% | 7.6% | 63.4% |
| layered_fan_out | 0.0% | 2.3% | 6.3% | 6.5% | 10.5% | 11.5% | 10.8% | 10.3% | 42.4% |
| over_invoicing | 0.0% | 5.0% | 10.0% | 2.5% | 20.0% | 50.0% | 55.0% | 7.5% | 80.0% |
| scatter_gather | 0.0% | 4.0% | 12.7% | 8.8% | 24.3% | 18.7% | 13.5% | 6.0% | 56.6% |
| single_large | 0.0% | 5.2% | 6.2% | 5.2% | 14.9% | 22.7% | 16.5% | 7.7% | 53.6% |
| smurfing | 0.0% | 8.8% | 8.5% | 5.7% | 8.5% | 14.2% | 13.3% | 69.2% | 79.5% |
| stacked_bipartite | 0.0% | 5.7% | 10.2% | 8.6% | 11.1% | 11.1% | 14.0% | 4.4% | 43.8% |
| structuring | 0.0% | 5.7% | 6.6% | 11.0% | 16.7% | 13.4% | 11.3% | 8.4% | 50.8% |
