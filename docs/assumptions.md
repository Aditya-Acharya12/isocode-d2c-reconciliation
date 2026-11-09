# Assumptions and open questions

The brief doesn't give the financial formulas, partner file formats or commercial terms. This document records what was assumed in their place, so nothing is silently invented. Everything marked **configurable** can change without a code change.

**Confirm** means the real client would need to answer this before relying on the numbers.

## Financial logic

### A1. Comparisons are made before tax
Reported TCS and TDS are added back to the amount received before comparing it with the expected amount. They're stored and displayed in separate columns and are never part of money at risk.

The sample shows ORD-M-88120 received ₹862 against ₹899, a difference of −₹37. We treat ₹862 as the pre-tax figure. The seed data shows ₹855.09 paid + ₹5.71 TCS + ₹1.20 TDS = ₹862.00, which reproduces the brief's figure and demonstrates the tax handling.

**Confirm:** whether "Received" in the client's reports is before or after tax.

### A2. A seventh status, Pending (confirmed)
An order delivered yesterday has no settlement yet. It isn't settled, it isn't past due, and calling it "Needs review" would bury real exceptions under about a month's normal orders. Pending covers orders not yet due or still in transit. It isn't a discrepancy and adds nothing to money at risk.

### A3. Seed rates are inferred from three examples
The sample rows fit simple percentages when rounded to the nearest rupee:

| Order | Calculation | Rounded | Brief |
|---|---|---:|---:|
| ORD-W-204118 | 1,499 × 94% = 1,409.06 | 1,409 | 1,409 |
| ORD-M-88120 | 1,199 × 75% = 899.25 | 899 | 899 |
| ORD-W-204377 | 2,398 × 4% = 95.92 | 96 | 96 |

Three points can't establish a formula. These are demo values in a **configurable** rate card, and no percentage is hardcoded. The engine supports percentages and fixed amounts, because ₹96 could just as easily be a flat charge, and that's how the demo card models it: a flat ₹96 RTO freight charge for Courier Aggregator A's 0.5 kg slab (A4).

The demo card ("Demo rate card" v1, effective 1 January 2026, added by the seed script) also needs values the samples don't imply. All of these are invented:

| Rule | Demo value |
|---|---|
| Website COD, delivered | 6%, remitted by the order's courier |
| Website prepaid, delivered | 2%, paid by the payment gateway |
| Marketplaces 1–3, delivered | 25%, paid by the marketplace |
| Quick commerce 1–2, delivered | 18%, paid by the platform |
| Any channel, RTO | Nothing expected in the settlement |
| Forward freight, Couriers A and B | ₹45 / ₹60 / ₹90 / ₹150 for 0.5 / 1 / 2 / 5 kg, plus 1.5% COD charge, minimum ₹30 |
| RTO freight, Couriers A and B | ₹96 / ₹110 / ₹150 / ₹220 |

**Confirm:** the actual rate cards for every partner.

### A4. ORD-W-204377 is a freight overcharge (confirmed for the demo)
The brief doesn't say what the −₹142 is. A website order has no marketplace commission, so the most plausible source is a return (RTO) freight charge of ₹142 against an expected ₹96. If the charge came through a settlement file instead, the same order would be Short-paid.

**Confirm with the real client:** the source of this charge.

### A5. Settlement and freight are compared separately
Each stream is compared with its own expected amount, and they are never added together, so a charge can't be counted twice. Rate rules state which deductions apply to each stream. This assumes freight isn't also deducted inside the percentages in A3.

**Confirm:** whether any partner nets freight out of its settlement.

### A6. The ₹10 threshold is strict
A difference of exactly ₹10.00 isn't flagged; ₹10.01 is. It applies per stream, per order, and is **configurable**. Variances under the threshold are still recorded, and the total is available, because many small differences can add up.

### A7. Overpayments aren't leakage
A positive difference over ₹10 goes to Needs review and isn't counted as money at risk. It may be a partner error the brand has to return, or a recovery of an earlier shortfall.

### A8. Rounding
Expected amounts round to the nearest rupee, half up, per A3. **Configurable** per rule. Actual amounts keep their paise.

### A9. Missing returns are valued at the order value
With no cost data available, an unreceived return is valued at its order value. That's a retail, GST-inclusive figure and so an upper bound. It's labelled as such, and the dashboard separates **cash** at risk from **inventory** at risk.

**Confirm:** whether unit costs can be provided.

### A10. Tax rates are reference data only
Leakage calculations use tax amounts **as reported** by the partner, never rates we calculate. Configured rates only flag a reported figure that looks wrong, and that flag isn't leakage.

| Item | Seed value | Note |
|---|---|---|
| GST TCS (CGST Act section 52) | 0.5% of net taxable value | **Sources conflict.** Some still say 1%. Needs checking against the official notification |
| Income tax TDS | 0.1% of gross amount | Formerly section 194-O; secondary sources place it in section 393(1) of the Income-tax Act, 2025 from 1 April 2026 |

Rates are effective-dated and **configurable**. Sources were mainly secondary, and this isn't tax advice.

**Confirm:** rates and treatment with the client's CA.

### A11. Rate cards apply by order date
The rate in force on the order date applies, not the settlement date.

**Confirm:** this matches partner contracts.

## Business and partners

### A12. A payment gateway exists but isn't in the brief
The brief lists marketplaces, quick commerce and couriers. Website prepaid money has to arrive from somewhere, so we add a payment gateway partner. It uses the same settlement file type.

**Confirm:** which gateway or gateways are used.

### A13. Quick-commerce settlements are order-level
Quick-commerce platforms may settle in aggregate, which order-level reconciliation can't handle. We assume order-level reports are available.

**Confirm:** with sample reports. This is the assumption most likely to be wrong, and would need an allocation design.

### A14. Payment terms are configurable per partner
Each partner has `settlement_terms_days`, which defines "past due", and `return_grace_days`. The demo values are invented: 7–15 days and 15 days. Aging counts from the due date. **Configurable.**

**Confirm:** the real terms.

### A15. Reconciliation is at order level
This follows the brief. A partial return of a multi-item order works only if the return record has a value or quantity. Otherwise it goes to Needs review. Reshipments under a new AWB are out of scope.

### A16. Reports aren't verified against the bank
A remittance shown in a partner's report counts as received. UTRs are stored to allow bank matching later, but a partner reporting a payment it never made wouldn't be detected.

**Risk to flag to the client:** this is a real gap, and bank statement matching is the natural next phase.

### A17. Single tenant, INR only
One brand and one currency.

## Roles

### A18. Separation of duties (confirmed)
Only the co-founder can change rate cards, and the co-founder has read-only access to the claims workflow. Since rate cards decide what counts as leakage, this keeps that control away from the person reconciling. See the matrix in [architecture.md](architecture.md#2-roles).

## Data handling

### A19. Duplicates are reported, not silently dropped
- An identical file is blocked before import.
- A row with a partner reference already loaded counts as a duplicate and appears in the import report.
- Without a reference, identical rows are treated as duplicates by hashing their canonical fields. Two genuine identical payments with no reference would collapse into one, so they appear in the report for a person to check.

### A20. Order files update; transaction files append
Delivery status legitimately changes between uploads, so a later order file updates the existing row. A blank cell in the later file doesn't erase a known value. The audit log records how many orders each import added and updated, but not each order's earlier values. Transactions are never overwritten.

### A28. One order file belongs to one sales channel
The channel is chosen at upload. A combined export covering several channels would need splitting first.

**Confirm:** the shape of the client's order exports.

### A29. Warehouse receipts count as a sixth file category
The brief's data list includes them, and detecting missing returns needs them. A warehouse is modelled as a partner of type `warehouse`.

### A30. Weights are in grams
Weight fields expect grams, with no unit conversion. A column in kilograms should be left unmapped, as its values would otherwise be read as grams.

### A31. Amounts with more than two decimals are rounded
They import rounded to paise by Postgres, with a warning on each such row.

### A21. Partial loads are allowed
Valid rows load and invalid rows are reported, instead of rejecting a whole file. A failure in the load itself rolls back everything.

### A22. Data sent to the AI provider
Only headers and masked samples are sent. Secondary sources say the Gemini free tier may use inputs for training and that paid tiers don't. Synthetic demo data is fine on the free tier, but **real client data needs a paid tier** and a review of Google's current terms.

## Reconciliation and claims

### A32. Who pays is part of the rate rule
Each settlement rule names the partner that pays. A rule with no payer means the order's courier, paid through COD remittances. Money that arrives by another route, such as a settlement for a COD order, or from another partner, goes to Needs review instead of being counted.

**Confirm:** how each channel's money actually reaches the brand.

### A33. Records link only when exactly one order matches
Records link by exact order ID, then a normalised order ID (case, spaces and punctuation ignored), then tracking ID. A partner tied to a sales channel only links to that channel's orders. A record matching no order, or several, stays unlinked and is listed, outside every total. A link, once made, is never changed by a later run.

### A34. A missing return is judged on the warehouse scan
A return is received only when the warehouse receipt file shows it. The courier's return status alone isn't enough, because a courier saying "delivered back" is exactly what this check doubts.

### A35. One claim per discrepancy
A claim covers one discrepancy, so one order and one partner. Claims are created in bulk and exported per partner, which gives the partner one file covering many orders. Only cash and missing-return discrepancies can be claimed. Needs review isn't a claim.

**Confirm:** whether partners want one claim reference per batch instead.

### A36. Recovery is entered by hand
Recovered amounts are typed in by finance as running totals. They aren't matched to a specific later settlement or credit note. If a recovery also arrives in an imported file, the run shows a smaller shortfall, and money at risk subtracts only the part of the typed-in amount the data doesn't yet show, so a recovery is never counted twice. Architecture section 8 has the exact rule.

### A37. Tax figures are summarised, not reconciled
The tax view totals the TCS and TDS partners report and counts lines with no tax reported. It doesn't compute what should have been withheld, because rates are reference data only (A10).

## Performance

### A23. What "100,000 rows in under a minute" measures
We read it as: from an approved import starting to all rows being committed, for one file. The brief doesn't say whether it covers reconciliation too, so reconciliation across 100,000 orders is measured and reported separately. It doesn't meet one minute: locally it took about 122 seconds on the first run and about 75 seconds on a rerun (see [benchmarks.md](benchmarks.md)). No document claims otherwise.

**Confirm:** whether the one-minute target was meant to include reconciliation.

Both CSV and XLSX get measured. The benchmark that tests the target runs **locally**, is reproducible from a script, and its report states the machine used. Deployed performance is reported separately if measured, including any failure. It's secondary to the local benchmark and won't delay implementation. Free-tier hosting isn't assumed to meet the target.

## Hosting

### A24. Free tiers only (confirmed)
The budget is ₹0: Vercel Hobby, Render Free and Neon Free. Render's free Postgres is excluded because it expires after 30 days. See [architecture.md](architecture.md#13-free-tier-deployment) for how the design handles each limit.

This is a demo setup, not a production recommendation. The first request after an idle period can take about a minute, and Neon suspends the database if monthly limits are exceeded. Vercel Hobby also doesn't permit commercial use, so a real client deployment needs paid plans.

### A25. Demo data volume
The deployed demo is kept small to stay well inside Neon's 1 GB limit. It currently holds 150 synthetic orders and no partner files. The 100k datasets are generated when needed and are never committed or loaded into the demo database.

### A26. AI mapping depends on time
Gemini is optional, as confirmed. Heuristic suggestions are always available, and are what runs unless `GEMINI_API_KEY` is set. The Gemini integration makes a real REST call with a structured output schema, but it has only been tested against mocked responses, because no key has been configured. It hasn't yet been exercised against the live API. If it isn't finished, the product describes its suggestions as rule-based and doesn't claim AI.

### A27. Node 22 is required for the frontend
The Tailwind build tooling used by Next.js 16 fails on Node 20.16, because it loads ES modules through `require()`. The project pins Node 22 in `.nvmrc` and `package.json`.

## Out of scope

Direct partner API integrations, bank statement matching, email sending to partners, multi-tenancy, line-item reconciliation, GST return filing support, and AI order-match suggestions.

## Sources for time-sensitive facts

Checked 5 November 2026. Secondary sources are noted above where relied on.

- [Neon: Free plan limits and quotas](https://neon.com/faqs/free-plan-limits-and-quotas)
- [Neon: plans](https://neon.com/docs/introduction/plans)
- [Neon: connection pooling](https://neon.com/docs/connect/connection-pooling)
- [Render: SSH and shell access](https://render.com/docs/ssh)
- [Render: deploys and pre-deploy commands](https://render.com/docs/deploys)
- [Vercel: Hobby plan](https://vercel.com/docs/plans/hobby)
- [Vercel: fair use guidelines](https://vercel.com/docs/limits/fair-use-guidelines)

- [TDS on e-commerce transactions, section 393(1) (TDSMan)](https://blog.tdsman.com/2026/07/tds-on-e-commerce-transactions-section-3931-194o/)
- [GST on e-commerce operators, section 52 (TaxAj)](https://www.taxaj.com/learn/gst-on-e-commerce-operators-tcs-under-section-52-step-by-step-guide/)
- [Render free tier documentation](https://render.com/docs/free)
- [Render pricing explained (Livemy)](https://livemy.app/blog/render-pricing)
- [Vercel limits](https://vercel.com/docs/limits/overview)
- [Vercel: body size limit on Functions](https://vercel.com/kb/guide/how-to-bypass-vercel-body-size-limit-serverless-functions)
- [Gemini API structured output](https://ai.google.dev/gemini-api/docs/json-mode)
- [Gemini API data use discussion (Simon Willison, 2024)](https://simonwillison.net/2024/Oct/17/gemini-terms-of-service)
