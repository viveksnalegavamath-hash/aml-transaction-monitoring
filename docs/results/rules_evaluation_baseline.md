# Rules evaluation — BASELINE (untuned plan values, run 25 Oct 2026)

Dataset: `marts` · generated 2026-10-25 10:48 IST · labels joined only after alerts exist.

Rule versions: R01 v1 (run 2), R02 v1 (run 3), R03 v1 (run 4), R04 v1 (run 5), R05 v1 (run 6), R06 v1 (run 7), R07 v1 (run 8), R09 v1 (run 9)

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
| R03 | One-to-many | 20,976 | 1,748.0 | 2.2% | 4.2× | 18.3% | 456 / 2,490 | 24.4% | 692 / 2,837 |
| R04 | Rapid pass-through | 10,817 | 901.4 | 3.0% | 5.9× | 10.4% | 260 / 2,490 | 20.3% | 575 / 2,837 |
| R05 | High-risk jurisdiction | 8,865 | 738.8 | 2.7% | 5.2× | 11.4% | 283 / 2,490 | 14.7% | 416 / 2,837 |
| R06 | Large vs profile | 45,666 | 3,805.5 | 2.7% | 5.2× | 49.7% | 1,237 / 2,490 | 52.8% | 1,499 / 2,837 |
| R07 | Behaviour change | 162,765 | 13,563.8 | 0.6% | 1.2× | 34.0% | 847 / 2,490 | 35.7% | 1,013 / 2,837 |
| R09 | Cash intensity | 8,763 | 730.2 | 3.3% | 6.5× | 16.5% | 412 / 2,490 | 17.3% | 492 / 2,837 |
| any rule |  | – | – | – | – | 72.9% | 1,816 / 2,490 | 81.9% | 2,323 / 2,837 |

### Rule × typology (transaction recall), hold-out (headline)

| Typology | R01 | R02 | R03 | R04 | R05 | R06 | R07 | R09 | any rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| behavioural_change_1 | 0.0% | 82.5% | 97.6% | 96.0% | 17.5% | 62.7% | 0.0% | 0.0% | 99.2% |
| behavioural_change_2 | 0.0% | 84.7% | 90.3% | 94.4% | 30.6% | 80.6% | 0.0% | 0.0% | 98.4% |
| bipartite | 0.0% | 3.6% | 3.6% | 3.6% | 45.5% | 52.7% | 47.3% | 7.3% | 80.0% |
| cash_withdrawal | 0.0% | 8.8% | 12.5% | 5.0% | 2.2% | 47.5% | 35.2% | 17.0% | 69.0% |
| cycle | 0.0% | 2.5% | 12.3% | 72.8% | 18.5% | 70.4% | 44.4% | 1.2% | 93.8% |
| deposit_send | 0.0% | 3.1% | 22.3% | 26.6% | 22.7% | 54.3% | 34.0% | 55.5% | 86.3% |
| fan_in | 0.0% | 13.1% | 17.9% | 1.2% | 38.1% | 57.1% | 54.8% | 4.8% | 95.2% |
| fan_out | 0.0% | 0.0% | 12.2% | 6.5% | 22.8% | 35.8% | 31.7% | 6.5% | 68.3% |
| gather_scatter | 0.0% | 3.3% | 20.3% | 13.8% | 18.7% | 43.1% | 36.6% | 5.7% | 75.6% |
| layered_fan_in | 0.0% | 0.6% | 16.5% | 16.5% | 14.7% | 68.8% | 46.5% | 5.9% | 84.1% |
| layered_fan_out | 0.0% | 2.3% | 19.2% | 0.8% | 6.2% | 44.6% | 39.2% | 7.7% | 76.2% |
| over_invoicing | 0.0% | 0.0% | 14.3% | 14.3% | 0.0% | 92.9% | 92.9% | 0.0% | 100.0% |
| scatter_gather | 0.0% | 6.9% | 35.6% | 3.4% | 29.9% | 67.8% | 56.3% | 2.3% | 93.1% |
| single_large | 0.0% | 7.1% | 8.9% | 8.9% | 16.1% | 64.3% | 75.0% | 14.3% | 96.4% |
| smurfing | 0.0% | 5.7% | 15.6% | 13.7% | 0.0% | 55.1% | 35.0% | 65.8% | 87.5% |
| stacked_bipartite | 0.0% | 6.3% | 28.3% | 11.0% | 6.3% | 49.7% | 45.0% | 7.9% | 78.5% |
| structuring | 0.0% | 6.9% | 17.5% | 11.9% | 15.5% | 42.8% | 32.7% | 7.2% | 77.8% |

## Tuning period

Base rate: 0.78% (5,737 suspicious of 736,925 active accounts).

| Rule | Name | Alerts | Alerts / week | Precision | Lift | Account recall | Accounts caught | Transaction recall | Transactions caught |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | Structuring | 0 | 0.0 | – | – | 0.0% | 0 / 5,737 | 0.0% | 0 / 7,036 |
| R02 | Many-to-one | 12,129 | 358.2 | 2.9% | 3.7× | 16.3% | 936 / 5,737 | 12.2% | 857 / 7,036 |
| R03 | One-to-many | 60,398 | 1,783.9 | 2.0% | 2.5× | 32.2% | 1,848 / 5,737 | 28.0% | 1,969 / 7,036 |
| R04 | Rapid pass-through | 25,749 | 760.5 | 2.8% | 3.6× | 13.2% | 759 / 5,737 | 19.1% | 1,344 / 7,036 |
| R05 | High-risk jurisdiction | 24,565 | 725.5 | 2.1% | 2.8× | 15.5% | 887 / 5,737 | 16.7% | 1,178 / 7,036 |
| R06 | Large vs profile | 80,201 | 2,368.8 | 2.5% | 3.2× | 58.4% | 3,351 / 5,737 | 41.2% | 2,898 / 7,036 |
| R07 | Behaviour change | 189,296 | 5,591.0 | 0.6% | 0.8× | 38.4% | 2,201 / 5,737 | 19.8% | 1,391 / 7,036 |
| R09 | Cash intensity | 33,111 | 978.0 | 2.3% | 3.0× | 34.2% | 1,961 / 5,737 | 19.3% | 1,358 / 7,036 |
| any rule |  | – | – | – | – | 84.8% | 4,867 / 5,737 | 75.5% | 5,313 / 7,036 |

### Rule × typology (transaction recall), tuning period

| Typology | R01 | R02 | R03 | R04 | R05 | R06 | R07 | R09 | any rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| behavioural_change_1 | 0.0% | 92.2% | 99.6% | 100.0% | 10.1% | 53.7% | 0.0% | 0.0% | 100.0% |
| behavioural_change_2 | 0.0% | 94.1% | 99.5% | 100.0% | 42.1% | 67.4% | 0.0% | 0.0% | 100.0% |
| bipartite | 0.0% | 7.3% | 22.3% | 8.2% | 22.0% | 43.3% | 27.4% | 14.0% | 72.6% |
| cash_withdrawal | 0.0% | 4.3% | 21.1% | 4.3% | 6.3% | 36.5% | 13.5% | 16.6% | 64.5% |
| cycle | 0.0% | 6.3% | 23.3% | 69.8% | 17.6% | 60.5% | 28.9% | 2.7% | 93.0% |
| deposit_send | 0.0% | 4.6% | 19.4% | 28.2% | 26.0% | 41.1% | 21.9% | 59.5% | 86.9% |
| fan_in | 0.0% | 26.1% | 22.5% | 16.1% | 12.9% | 30.7% | 23.6% | 4.6% | 79.6% |
| fan_out | 0.0% | 0.0% | 42.1% | 9.6% | 50.0% | 42.1% | 21.9% | 7.0% | 83.3% |
| gather_scatter | 0.0% | 2.6% | 38.1% | 8.2% | 32.5% | 39.8% | 27.7% | 8.2% | 81.0% |
| layered_fan_in | 0.0% | 5.1% | 23.9% | 8.2% | 15.4% | 55.1% | 28.8% | 7.6% | 77.6% |
| layered_fan_out | 0.0% | 2.3% | 21.8% | 6.5% | 10.5% | 30.6% | 19.5% | 10.3% | 61.2% |
| over_invoicing | 0.0% | 5.0% | 30.0% | 2.5% | 20.0% | 50.0% | 55.0% | 7.5% | 85.0% |
| scatter_gather | 0.0% | 4.0% | 23.1% | 8.8% | 24.3% | 50.6% | 31.9% | 6.0% | 76.5% |
| single_large | 0.0% | 5.2% | 14.4% | 5.2% | 14.9% | 45.4% | 35.1% | 7.7% | 73.7% |
| smurfing | 0.0% | 8.8% | 25.7% | 5.7% | 8.5% | 41.9% | 17.2% | 69.2% | 85.1% |
| stacked_bipartite | 0.0% | 5.7% | 21.0% | 8.6% | 11.1% | 30.8% | 19.0% | 4.4% | 61.3% |
| structuring | 0.0% | 5.7% | 20.5% | 11.0% | 16.7% | 32.6% | 16.6% | 8.4% | 64.4% |
