# Sanctions screening (S01) — results

UK Sanctions List dated **02 Oct 2026** (FCDO, OGL v3.0) · threshold **90** · spec version 2 · run took 9.7 min.

The KYC customers are SYNTHETIC (made-up names), so every KYC match is a false match by construction. The labelled test set shows that true matches are found.

## Matches per 10,000 customers

| Customers | Screened | Customers matched | Per 10,000 | Match pairs | High | Medium | Low |
| --- | --- | --- | --- | --- | --- | --- | --- |
| individual | 248,896 | 171 | 6.9 | 263 | 16 | 93 | 154 |
| business | 43,819 | 67 | 15.3 | 69 | 1 | 66 | 2 |
| all | 292,715 | 238 | 8.1 | 332 | 17 | 159 | 156 |

Priority = name score + evidence points (date of birth, nationality / country of incorporation, low-quality alias); High >= 100, Medium >= 90. No match is dropped for its evidence.

## Labelled test set: variations of real list names caught

2,883 variations of 500 real list names (schema `screening_test`, separate from the KYC table). Caught = scores at least the threshold against its own designation.

| Variation | Type | Tested | Caught at 85 | **Caught at 90** | Caught at 95 |
| --- | --- | --- | --- | --- | --- |
| exact | Individual | 400 | 100.0% | **100.0%** | 100.0% |
| exact | Entity | 100 | 100.0% | **100.0%** | 100.0% |
| exact | all | 500 | 100.0% | **100.0%** | 100.0% |
| formatting | Individual | 400 | 100.0% | **100.0%** | 100.0% |
| formatting | Entity | 100 | 100.0% | **100.0%** | 100.0% |
| formatting | all | 500 | 100.0% | **100.0%** | 100.0% |
| spelling | Individual | 400 | 100.0% | **99.2%** | 77.8% |
| spelling | Entity | 100 | 98.0% | **92.0%** | 74.0% |
| spelling | all | 500 | 99.6% | **97.8%** | 77.0% |
| word_order | Individual | 400 | 100.0% | **100.0%** | 100.0% |
| word_order | Entity | 100 | 100.0% | **100.0%** | 100.0% |
| word_order | all | 500 | 100.0% | **100.0%** | 100.0% |
| missing_middle | Individual | 302 | 100.0% | **100.0%** | 100.0% |
| missing_middle | Entity | 81 | 79.0% | **77.8%** | 77.8% |
| missing_middle | all | 383 | 95.6% | **95.3%** | 95.3% |
| spelling_and_order | Individual | 400 | 100.0% | **99.8%** | 77.5% |
| spelling_and_order | Entity | 100 | 99.0% | **92.0%** | 76.0% |
| spelling_and_order | all | 500 | 99.8% | **98.2%** | 77.2% |
| **all** | all | 2,883 | 99.3% | **98.7%** | 91.4% |

Missed at the threshold (closest first):

| Variation | Test name | Listed name | Score |
| --- | --- | --- | --- |
| spelling | SEmer Foz | Samer Foz | 89 |
| spelling | MIlek Ali | Malek Ali | 89 |
| spelling_and_order | FZE CASTLETON CORPORATIO | CASTLETON CORPORATION FZE | 89 |
| spelling | LLC TOP ALLOI | LLC TOP ALLOY | 89 |
| spelling_and_order | ALLOI LLC TOP | LLC TOP ALLOY | 89 |
| spelling | RIBAR LLC | RYBAR LLC | 89 |

## False-match examples (SYNTHETIC customers)

| Customer (synthetic) | Type | Matched list name | Name type | Name score | DOB | Nationality | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Kim Young | individual | Young-Nam Kim | primary_variation | 100 | year | unknown | High (105) |
| Mohammad Ali | individual | Ali Mohammad Naderi | primary_variation | 100 | unknown | unknown | High (100) |
| Mohammad Ali | individual | Mohammad Ali Zanjirei | primary | 100 | unknown | unknown | High (100) |
| Mohammad Ahmed | individual | Ahmed Mohammad Bustaji | primary | 100 | unknown | unknown | High (100) |
| Abdul Hall | individual | Abdul Khalil | alias (low) | 91 | conflict | conflict | Low (71) |
| Paul Long | individual | Paul Malong | alias | 90 | conflict | conflict | Low (75) |
