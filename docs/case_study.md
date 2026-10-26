# Worked investigation case: cash in, money out abroad

> **SYNTHETIC TRAINING EXAMPLE — NOT A REAL CASE AND NOT A REAL SAR.**
> The customer, accounts and transactions are synthetic: transactions from SAML-D (a synthetic dataset), the customer profile generated with Faker (seed 20261014). The customer is not a real person, and no name from the UK Sanctions List is used. The bank, the analysts and the review dates are illustrative. The legal points summarise the UK process for training; they are not legal advice.

| | |
| --- | --- |
| Customer | 7218608806 — "Nicola Moore" (synthetic), individual, skilled tradesperson |
| Case opened by | Alert `8f4b0baf`, R03 one-to-many (version 2, tuned), 8 Jul 2023 |
| Other alerts on the customer | 11 more in the hold-out months, from R04, R05 and R09 (13 earlier, Oct 2022 – May 2023) |
| Pattern found | Five large cash deposits, each matched within 0–2 days by a payment of nearly the same amount (within 2.5%) to a **new** beneficiary, four of them abroad (Morocco, Nigeria, Pakistan, Albania) |
| Decision | L1 → escalate · L2 → internal disclosure to the MLRO · MLRO → SAR to the NCA (SAR Portal), 24 Jul 2023; a further SAR after the activity continued |
| Outcome check (evaluation only) | The ten transactions in the five pairs are exactly the ten labelled `deposit_send` (laundering) in SAML-D; everything else on the account is labelled normal |

## 1. How the case was chosen

- **Period:** the hold-out months (1 Jun – 23 Aug 2023), the same months as the headline results, using the tuned rule set (R03, R06, R07 version 2; the other rules version 1).
- **"High priority":** the project has no alert-priority score yet (customer risk scoring is future work). For this case, high priority means **alerts from at least three different rules on the same customer within 30 days, including a tuned rule**. On 8 Jul 2023 this customer had six alerts from four rules in 30 days.
- **Use of the label:** the laundering label was used **only to pick a true positive** for the walkthrough, so the case can be checked against the ground truth at the end. The label never enters the rules or model features (CLAUDE.md rule 3), and the analysts in the walkthrough do not see it. The customer with the most rules firing, chosen without the label, turned out to be a false positive; it is shown in section 8, because most alerts look like that one.

## 2. The alert and why it fired

**Alert `8f4b0baf` — R03 one-to-many, version 2, 8 Jul 2023 19:18**

> Paid £137,264.29 to 10 different accounts in 23 payments within 7 days (rule: at least 10 receivers and £100,000).

R03 looks for money spread across many receivers in a short time (fan-out), a common layering pattern. Version 2 added the £100,000 minimum during tuning (`rules/R03_one_to_many.yaml`, change log). The 23 payments (2–8 Jul) went to:

| Receiver | Payments | Amount | Known payee before June 2023? |
| --- | --- | --- | --- |
| 2138646789 (UK) | 12, all on 3 Jul, £5,011–£5,193 each | £61,061.35 | **No — new** |
| 1271703404 (UK) | 2 | £30,003.31 | Yes |
| 9716035031 (UK) | 1 | £14,989.43 | Yes |
| 6908275712 (Nigeria) | 1 | £9,904.28 | **No — new** |
| 7602605014 (Nigeria) | 1 | £6,280.30 | Yes (14 payments since Oct 2022) |
| Five other UK payees | 6 | £15,025.62 | Yes |

**Every alert on this customer in the hold-out months** (the six above the line were open when L1 picked up the case; the rest arrived later):

| Date | Alert | Rule | What fired |
| --- | --- | --- | --- |
| 9 Jun | `534b87ae` | R05 high-risk jurisdiction | £11,174.49 to Nigeria (internal rating high) — usual beneficiary |
| 19 Jun | `9f685415` | R09 cash intensity | Cash £8,230.63 (1 deposit) = 100% of inflows in 30 days |
| 3 Jul | `28252e2b` | R09 | Cash £9,439.15 (2 deposits) = 100% of inflows |
| 5 Jul | `eb7dacfd` | R04 rapid pass-through | Received £10,980.29 in 2 payments, sent £54,019.52 (492%) in 12 within 48 hours |
| 6 Jul | `2c1c134b` | R05 | £9,904.28 to Nigeria — **new** beneficiary |
| 8 Jul | `8f4b0baf` | **R03 v2 (tuned)** | **Case trigger** (above) |
| *14 Jul* | `6686eb70` | R09 | Cash £29,208.01 (5 deposits) = 100% of inflows |
| *15 Jul* | `32dbca90` | R05 | £15,321.57 to Pakistan (internal rating high) — new beneficiary |
| *26 Jul* | `9027e2ce` | R09 | Cash £37,371.85 (6 deposits) |
| *6 Aug* | `cb52234d` | R09 | Cash £38,962.23 (5 deposits) |
| *8 Aug* | `9e04c42e` | R04 | Received £12,570.67, sent £15,710.70 (125%) within 48 hours |
| *17 Aug* | `5a2467b4` | R09 | Cash £15,708.66 (3 deposits) |

Hold-out precision of these rules is 2–4% (R03 2.1%, R04 3.0%, R05 2.7%, R09 3.3%; `docs/results/rules_evaluation.md`): on its own, each alert is far more likely to be a false positive than a true one. What raises the priority is several different rules firing on one customer.

## 3. Customer profile (KYC, synthetic)

| Field | Value |
| --- | --- |
| Name | Nicola Moore (synthetic) |
| Customer type | Individual |
| Date of birth | 26 Mar 1965 |
| Nationality | GB (internal country rating: low) |
| Occupation | Skilled tradesperson |
| Declared annual income | £30,000 |
| Customer since | 5 Apr 2013 |
| PEP | No |
| Sanctions screening (S01) | No match (UK Sanctions List dated 02 Oct 2026) |
| Address, ID documents, expected activity | Not generated (no such fields in this project) |

## 4. Transaction history

**Before the hold-out months (Oct 2022 – May 2023, eight months):**

- **Payments out:** 196 payments to 25 payees, £824,396.33 in total, median £1,873. Cross-border payments went to **one** beneficiary only: account 7602605014 in Nigeria (14 payments, £111,647.67), a steady pattern since the first month of the data.
- **Money in:** cash deposits only, 9 of them, £23,234.16 in total, largest £5,824.10.
- Earlier alerts: 13 (8 × R05 on the usual Nigerian beneficiary, 4 × R09, 1 × R03). They were never worked: this project has no case-management workflow. In a bank, L1 would read their closure notes.

**In the hold-out months, the pattern changes.** Five large cash deposits, each paired with a payment of nearly the same amount to a payee **never used before**:

| Pair | Cash deposit | Payment out | New beneficiary | Gap | Payment vs deposit |
| --- | --- | --- | --- | --- | --- |
| 1 | 19 Jun 17:42 · £8,230.63 | 19 Jun 08:37 · £8,080.37 | 1478271222, **Morocco** | same day (payment first) | −1.8% |
| 2 | 4 Jul 22:08 · £9,771.77 | 6 Jul 21:17 · £9,904.28 | 6908275712, **Nigeria** | 47 h | +1.4% |
| 3 | 14 Jul 11:29 · £8,658.97 | 14 Jul 13:58 · £8,849.49 | 915996604, UK (card payment) | 2.5 h | +2.2% |
| 4 | 15 Jul 14:42 · £14,942.52 | 15 Jul 04:33 · £15,321.57 | 4636306668, **Pakistan** | same day (payment first) | +2.5% |
| 5 | 6 Aug 10:17 · £12,570.67 | 8 Aug 01:41 · £12,334.28 | 6604063927, **Albania** | 39 h | −1.9% |
| | **£54,174.56** | **£54,489.99** | | | |

The customer's other cash deposits in these months stay small and regular (£1,209–£1,686, as before).

*Data limitation:* SAML-D has no account balances and does not contain all of an account's funding, so outflows cannot be reconciled with inflows (here £824k out against £23k in before June). The analysts do not use that gap as evidence.

## 5. L1 review — Monday 10 Jul 2023

The L1 analyst works the customer's six open alerts as one case (alerts are grouped per customer).

**L1 analyst's notes**

> - **R03 (trigger):** 23 payments to 10 payees in 7 days. Eight of the ten payees were already in use before June, including the regular Nigerian beneficiary. Two are new: a UK payee that received 12 payments of about £5,100 on 3 Jul (£61,061), and a Nigerian account (£9,904.28 on 6 Jul). On its own the R03 alert would be **closed as consistent with history**, apart from the two new payees.
> - **R05, 9 Jun:** usual Nigerian beneficiary, in line with 14 earlier payments. Consistent with history.
> - **R05, 6 Jul:** **new** Nigerian beneficiary, paid £9,904.28 two days after a £9,771.77 cash deposit (4 Jul). Not explained by history.
> - **R09, 19 Jun and 3 Jul:** a cash deposit of £8,230.63 on 19 Jun, larger than any earlier deposit (previous maximum £5,824.10); a payment of £8,080.37 to a new Moroccan beneficiary went out the same day. A second large deposit (£9,771.77) followed on 4 Jul.
> - **R04, 5 Jul:** driven mainly by the ~£5,100 payments of 3 Jul, made after a small deposit. Not a clean pass-through on its own, but the window also holds the £9,771.77 deposit.
> - **KYC:** skilled tradesperson, £30,000 a year. Cash income is plausible for a trade. Sending each large deposit abroad to a different new person within days is not explained by the profile.
> - **Decision: ESCALATE to L2.** Reason: two large cash deposits, each matched by a near-equal payment to a new overseas beneficiary within 0–2 days, in different countries, one of them rated high risk. Not explained by the customer's history or profile. Time spent: 35 min.

## 6. L2 investigation — 11 to 21 Jul 2023

L2 reviews the whole history in the data (Oct 2022 onwards) and the two new alerts that arrive during the investigation (R09 on 14 Jul, R05 on 15 Jul).

**What L2 did**

1. **Rebuilt the timeline** of every deposit over £5,000 and every payment to a new payee. This produced the pairs in section 4: by 21 Jul, four pairs (19 Jun – 15 Jul), £41,603.89 in cash in and £42,155.71 out.
2. **Counterparty analysis.** Each of the four beneficiaries is new, is paid **once**, and is in a different place: Morocco, Nigeria, the UK (card payment), Pakistan. Nigeria and Pakistan are rated high risk internally (World Bank governance indicators); Morocco is medium; none is on the FATF lists used by the project.
3. **Checked the innocent explanations.**
   - *Cash takings from a trade:* fits the deposits, but not each deposit being forwarded at once, in full, to a different new person.
   - *Family remittances:* the long-running payments to one Nigerian account fit this, and L2 treats them as normal. The new pattern is the opposite: one payment per beneficiary, several countries, amounts tied to the cash.
   - *Paying suppliers:* a tradesperson's suppliers would normally be domestic and repeat; these are mostly overseas and one-off.
4. **Considered contacting the customer.** In a bank, L2 would normally ask for the source of the cash and the purpose of the payments, worded as routine customer due diligence, without mentioning suspicion: telling a customer that a SAR is being considered can be **tipping off** (POCA 2002 s.333A). Not possible here (synthetic customer), so no explanation was received.
5. **Recorded what was not used as evidence:** the outflow-versus-inflow gap (no balances in the data); the 12 payments of ~£5,100 to a new UK payee on 3 Jul (unusual, noted, but no link to the cash, so outside the core suspicion); the declared income (synthetic and generated independently of the transactions).

**L2 conclusion (21 Jul 2023)**

> Four times in four weeks, a cash deposit of £8,230–£14,943 was matched within two days by a payment within 2.5% of the same amount to a beneficiary never paid before, three of them abroad and two in countries rated high risk. The pattern suggests the account is being used to move cash of unknown origin abroad (placement followed by layering). No legitimate explanation is on file. **I suspect the funds may be criminal property.** Internal suspicious activity report made to the MLRO. Recommendation: SAR; keep the account under enhanced monitoring; relationship review.

This internal report is how the analyst meets their own obligation to disclose (POCA 2002 s.330: in the regulated sector, failing to report knowledge or suspicion of money laundering, or reasonable grounds for it, is an offence; reporting to the nominated officer meets the obligation).

## 7. MLRO decision and the SAR — Monday 24 Jul 2023

The MLRO (the bank's nominated officer; FCA senior management function SMF17) decides independently whether to report to the National Crime Agency (POCA 2002 s.331).

**MLRO decision log**

> - Internal report from L2, 21 Jul 2023, reviewed with the evidence pack (alerts, timeline, counterparty table).
> - Suspicion threshold met: the pattern is specific and repeated, and the innocent explanations considered do not fit.
> - **Decision: submit a SAR to the NCA through the SAR Portal.** Submitted 24 Jul 2023.
> - **DAML not required:** all four pairs are completed transactions and no transaction is waiting for our action. If a new payment of this kind is requested, it is to be held and a Defence Against Money Laundering request made before it is processed.
> - Account flagged for enhanced monitoring; relationship review opened (exit decision to be taken separately, without tipping off).
> - Staff reminded: do not tell the customer about the SAR or the investigation (s.333A).
> - Records kept for at least five years (Money Laundering Regulations 2017, reg. 40).

Had a DAML been needed, the timings are: the NCA has a **7 working day notice period** to refuse; if it refuses, a **31 day moratorium** follows, which a court can extend (up to 186 days in total, Criminal Finances Act 2017). Silence at the end of the notice period means the bank has a defence to proceed.

### SAR narrative (SYNTHETIC TRAINING EXAMPLE)

> **Reason for suspicion.**
>
> **Who.** Our customer Nicola Moore, date of birth 26/03/1965, British national, occupation skilled tradesperson, declared income £30,000 a year, customer since April 2013, account 7218608806. Also four beneficiaries the customer had not paid before: account 1478271222 (Morocco), account 6908275712 (Nigeria), account 4636306668 (Pakistan) and account 915996604 (UK). We hold no further details on the beneficiaries.
>
> **What.** Four cash deposits totalling £41,603.89, each matched within two days by a single payment of almost the same amount (difference 1.4%–2.5%) to a different new beneficiary, totalling £42,155.71:
> - 19/06/2023: cash £8,230.63; same day, £8,080.37 to 1478271222 (Morocco).
> - 04/07/2023: cash £9,771.77; 06/07/2023, £9,904.28 to 6908275712 (Nigeria).
> - 14/07/2023: cash £8,658.97; same day, card payment of £8,849.49 to 915996604 (UK).
> - 15/07/2023: cash £14,942.52; same day, £15,321.57 to 4636306668 (Pakistan).
>
> **Where.** Cash paid into the customer's UK account (branch not recorded); funds sent to Morocco, Nigeria and Pakistan by cross-border transfer, and within the UK by card payment.
>
> **When.** 19 June 2023 to 15 July 2023.
>
> **Why we are suspicious.** Before June 2023 the customer's largest cash deposit was £5,824.10, and their only overseas payee was one long-standing beneficiary in Nigeria. From June 2023 each large cash deposit is forwarded almost in full, within two days, to a different person never paid before, in three countries. The amounts track the cash closely, which suggests the account is passing on cash for others rather than receiving trade income. We have no information on the source of the cash or the purpose of the payments. We suspect the cash may be the proceeds of crime and the account is being used to move it abroad.
>
> **Action taken.** Account under enhanced monitoring; relationship under review. No transaction is pending; no DAML is requested.

## 8. After the SAR — 6 to 17 Aug 2023

- **The pattern continued:** cash £12,570.67 on 6 Aug, and £12,334.28 to a new beneficiary in **Albania** (6604063927) on 8 Aug (R09 and R04 alerts).
- **What should have happened:** after the first SAR, the account should carry a payment hold, so the 8 Aug payment is stopped and a DAML requested before it is released. Processing it while already suspecting the funds risks a principal money-laundering offence (POCA 2002 ss.327–329). This project has no real-time payment hold (future work), so in the data the payment went through.
- **Response:** a further SAR describing the new pair and quoting the first SAR's reference number; the relationship review moves to an exit decision.

## 9. Outcome check (evaluation only)

The laundering labels were joined only for this section.

- **All ten transactions in the five pairs are labelled `deposit_send` (laundering), and nothing else on the account is.** The L2 timeline isolated exactly the laundering transactions.
- **The case trigger itself was on normal activity.** R03's 23 payments include the second pair's Nigerian payment, but most are normal; the 12 payments of ~£5,100 on 3 Jul are labelled normal (`normal_periodical`). L2 was right to leave them out of the suspicion. Looking at the **customer as a whole**, rather than the single alert, is what found the case.
- **Which rules saw it:** R09 flagged the deposits; R05 flagged the Nigeria and Pakistan payments; R04 flagged the Albania pair. The Morocco payment raised no R05 alert, because Morocco is rated medium. In the hold-out months, "any rule" caught 77.7% of `deposit_send` transactions.

**The contrast: what most alerts look like.** The customer with the most rules firing in the hold-out months, chosen without the label, was account 3160196899 (synthetic, "unemployed", declared income £7,000). All seven enabled rules fired (11 alerts): on 27 Jul 2023 the account made 263 payments (£2,480,920) in a single day, including 13 near-identical payments of about £8,350 to one account in Pakistan, and on 6 Aug it received 132 payments (£1,521,546). An L1 analyst would very likely escalate it. In SAML-D **every transaction on that account is labelled normal** (`normal_fan_out`, `normal_fan_in`). More rules firing does not mean more suspicious. With rule precision at 2–5%, ranking alerts better is the job of the ML layer (next step).

## 10. Limits of this example

- **Synthetic data.** The KYC profile was generated independently of the transactions (see R10 and "Synthetic KYC" in `LEARNING.md`), so any agreement or mismatch between profile and activity here is coincidental. In real cases, the KYC profile is central evidence.
- **Missing fields.** SAML-D has no balances, branches, beneficiary names or payment references. Cash deposits carry a counterparty account ID, which is not used here.
- **Lists from a later date.** Country risk (FATF June 2026, World Bank WGI 2025) and the UK Sanctions List (02 Oct 2026) are applied to 2023 activity.
- **Illustrative review.** The review dates, times spent and decision logs are illustrative; in this project, alerts are generated but not worked.

## UK process references

- Proceeds of Crime Act 2002: ss.327–329 (principal money-laundering offences), s.330 (failure to disclose, regulated sector), s.331 (nominated officer), s.333A (tipping off), ss.335–336 and 336A (defence against money laundering: notice period, moratorium, extensions).
- The Money Laundering, Terrorist Financing and Transfer of Funds (Information on the Payer) Regulations 2017 (nominated officer; record keeping, reg. 40).
- FCA Handbook SYSC 6.3 (financial crime systems and controls; the MLRO).
- JMLSG Guidance, Part I, Chapter 6 (suspicious activity, reporting and data protection).
- National Crime Agency: SAR Portal, and guidance on writing good-quality SARs.
