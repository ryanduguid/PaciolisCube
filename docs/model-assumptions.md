# Model assumptions

Statutory figures last verified 23 August 2026. They go stale. Read
[Sample data, not a maintained rate table](#sample-data-not-a-maintained-rate-table)
before you rely on any number here.

## What the model is

PaciolisCube plans a fictional Australian mining services group. Two operating
entities, CivilCo (civil earthworks) and HaulCo (haulage), roll up to a `Group`
consolidation. The financial year ends 30 June. Two years are held: FY2025-26 as
`Actual` and FY2026-27 as `Budget`. Amounts are Australian dollars.

Every entity, cost centre, role, rate and volume in `examples/` is invented. No
real employer, client or person is referenced, and no client data was used to
build or test the model.

## What the model is not

- Not tax advice, and not a payroll or lodgement engine. The superannuation and
  payroll tax rules here are a planning approximation, not a calculation you
  could file.
- Not a maintained rate table. The statutory figures are sample data pinned at
  the retrieval date above.
- Not a full set of accounts. There is no balance sheet, no cash flow, no income
  tax, no GST, no foreign currency and no intercompany elimination.

## Where the statutory figures live

No rule file holds a statutory literal. Every rate and threshold is read from the
`Drivers` cube with `DB()`, and the values arrive from `examples/drivers.csv`
through `model/processes/LoadDrivers.ti`.

`tests/test_model_rules.py` fails the build if a number written in the text of
`Workforce.rules`, `Revenue.rules`, `Capex.rules` or `PnL.rules` carries a value
the driver file ships. It reads those values out of `examples/drivers.csv`
rather than from a list kept by hand, so a driver added or repriced later is
guarded from the day it ships. It reads a percentage sign too, so `12%` fails
the build as surely as `0.12`, and it reads thousands separators, so `260,280`
fails as surely as `260280`.

Two kinds of figure escape it, because rule text writes both for reasons of its
own. A driver shipping at zero or one cannot be told apart from arithmetic. A
rate written as a whole number of percent, `12` for 12%, cannot be told apart
from the twelfths these rules divide by. Write a rate as a fraction or with a
percentage sign and the guard sees it.

## Statutory drivers

| Driver | Year | Value in `examples/drivers.csv` | Source | Retrieved | Status |
| --- | --- | --- | --- | --- | --- |
| SG Rate | FY2025-26 | 0.12 | [1] s 19(2), table item for a year starting on or after 1 July 2025 | 2026-08-23 | Verified against primary source |
| SG Rate | FY2026-27 | 0.12 | [2] s 17A(2), which reads "charge percentage means 12" | 2026-08-23 | Verified against primary source |
| Maximum Contribution Base | FY2026-27 | 270830 | [2] s 10A(5) and s 10A(6) | 2026-08-23 | Formula and annual basis verified against primary source. The basic concessional contributions cap input needs one human confirmation. See note A |
| Maximum Contribution Base | FY2025-26 | 260280 | [1] s 15, which sets a quarterly base, not this annual one | 2026-08-23 | Does not reconcile. Appears to be the FY2024-25 figure. Needs one human confirmation and probably a correction. See note B |
| Payroll Tax Rate | FY2025-26 and FY2026-27 | 0.0545 | [3] | 2026-08-23 | Verified against primary source |
| Payroll Tax Threshold | FY2025-26 and FY2026-27 | 1200000 | [3] | 2026-08-23 | Verified against primary source |

### Note A: how 270830 is built

From 1 July 2026 the Treasury Laws Amendment (Payday Superannuation) Act 2025,
Act No. 57 of 2025, replaced the quarterly maximum contribution base with an
annual one. The new provision spells it "maximum contributions base", so search
for that if you go looking. Section 10A(5) of [2] sets the maximum
contributions base for a payment of qualifying earnings as the concessional
contributions cap multiplied by 100 and divided by the charge percentage,
rounded down to the nearest multiple of $10. It defines that cap as the basic
concessional contributions cap, within the meaning of the Income Tax Assessment
Act 1997, for the financial year in which the payment is made.
Section 10A(6) applies that base to the employee's total qualifying earnings
during the financial year in relation to the employer, which is what makes it an
annual ceiling rather than a quarterly one. Section 17A(2) sets the charge
percentage at 12.

With a basic concessional contributions cap of $32,500, the formula gives
$32,500 x 100 / 12 = $270,833.33, rounded down to $270,830. That matches the
shipped value exactly.

The $32,500 cap is the one input not verified here. It is the indexed basic
concessional contributions cap published by the Australian Taxation Office, and
`ato.gov.au` returned HTTP 403 to every automated fetch from this machine on
23 August 2026. Secondary commentary is consistent on $32,500, and the exact
arithmetic match is strong circumstantial support, but a person should confirm
the cap on the ATO rates and thresholds page before the number is trusted.

### Note B: the FY2025-26 figure does not reconcile

For FY2025-26 the maximum contribution base was still a quarterly amount, not an
annual one. Section 15 of [1] sets it: subsection (3) indexes the previous
year's amount, and subsection (5) substitutes an amount worked out from the
basic concessional contributions cap and the charge percentage in s 19(2) when
that amount is the lower of the two. The Act carries the formula, not the dollar
figure, which the Australian Taxation Office publishes.

Secondary sources put that figure at $62,500 per quarter, which is $250,000 for
the year. The shipped 260280 is four times $65,070, and $65,070 per quarter was
the FY2024-25 base. The value looks like it was carried over one year too far.

Neither the FY2025-26 nor the FY2024-25 quarterly figure could be confirmed
against a primary source from this machine, for the same ATO 403 reason. Both
the diagnosis and the replacement value need one human check.

The row is `FY2025-26`, `Actual`, `Full Year`, `Maximum Contribution Base` in
`examples/drivers.csv`.

While the figure stands uncorrected the FY2025-26 comparative caps earnings
$10,280 too high for each capped employee, which at 12% overstates the
superannuation charge by $1,233.60 a head for the year. Payroll tax rides on top
of that. The payroll tax statement in `model/cubes/Workforce.rules` levies the
rate on base pay plus superannuation cost, so the overstated superannuation
drags payroll tax up by a further $1,233.60 at 5.45%, being $67.23 a head, and
total employment cost by $1,300.83. The threshold credit in
`model/cubes/PnL.rules` is a fixed monthly amount that does not move with wages,
so the $67.23 reaches the group total rather than being absorbed.

The cap binds in the shipped data: the CivilCo Drill and Blast Supervisor
`Base Pay Rate` row in `examples/workforce.csv` is $290,000, above the base on
either figure, so the error changes a computed result rather than sitting inert.
That role is the only one paid above either base in FY2025-26, and it carries a
headcount of one in all twelve months, so the $1,300.83 is also the whole group
overstatement for the year.

## Planning drivers

These are invented planning assumptions with no external source. They exist to
make the model run and to give the seeding process something to uplift.

| Driver | FY2025-26 | FY2026-27 | Used by |
| --- | --- | --- | --- |
| Indexation | 0.00 | 0.03 | `model/processes/SeedBudget.ti`, on two of the four measures it carries forward |
| Fuel Price | 1.78 | 1.85 | `Revenue.rules`, multiplied by planned litres |
| Utilisation | 0.72 | 0.75 | Nothing. No rule or process reads it |

`SeedBudget.ti` copies four `Revenue` measures from the source year and applies
the target year's `Indexation` to two of them, `Charge Rate` and `Plant Hire
Revenue Amount`. `Billable Hours` and `Fuel Litres` carry forward unchanged, so
budget volumes equal actual volumes and only price moves.

`Plant Hire Revenue Amount` is the exception worth knowing about. It is a dollar
amount, not a rate, so uplifting it moves plant hire revenue by the indexation
factor whatever the reason for the change, and there is no volume underneath it
to plan separately. Uplifting it is a choice about the seed, not a price
assumption.

## Sources

1. Superannuation Guarantee (Administration) Act 1992 (Cth), compilation No. 76,
   registered as C2022C00095, compilation date 23 February 2022, the version in
   force on 1 July 2025.
   https://www.legislation.gov.au/C2004A04402/2025-07-01/2025-07-01/text/original/epub/OEBPS/document_1/document_1.html
2. Superannuation Guarantee (Administration) Act 1992 (Cth), compilation No. 78,
   registered as C2026C00272, compilation date 1 July 2026, incorporating the
   Treasury Laws Amendment (Payday Superannuation) Act 2025, Act No. 57 of 2025,
   assented to 6 November 2025.
   https://www.legislation.gov.au/C2004A04402/2026-07-01/2026-07-01/text/original/epub/OEBPS/document_1/document_1.html
   The compilation numbers and registration codes above come from the version
   list at https://www.legislation.gov.au/C2004A04402/versions
3. Revenue NSW, payroll tax thresholds and rates.
   https://www.revenue.nsw.gov.au/taxes-duties-levies-royalties/payroll-tax/lodge-and-pay-returns/thresholds-and-rates
4. Superannuation Guarantee Ruling SGR 2009/2, the Commissioner's ruling on
   ordinary time earnings and salary or wages, published in the Australian
   Taxation Office legal database. **Not read at source.** Both `ato.gov.au` and
   `austlii.edu.au` returned HTTP 403 to automated fetches on 23 August 2026.
   Treat anything carrying [4] as unverified. The overtime point is also carried
   by the s 6 definition in [1], which was read at source; the leave loading
   point rests on the ruling alone.
5. Payroll Tax Act 2007 (NSW), Division 7 of Part 3 for relevant contracts, and
   the wages provisions for termination payments, fringe benefits and employee
   share scheme grants. **Not read at source.** `legislation.nsw.gov.au` returns
   HTTP 403 to automated fetches. Treat anything carrying [5] as unverified,
   including the Division and Part numbers.

Everything cited to [1] and [2] was read at source on 23 August 2026: s 15,
s 15A, s 19(1) and s 19(2) in [1], and s 10A(1), s 10A(5), s 10A(6) and s 17A(2)
in [2]. The formulas in s 15(3) and s 15(5) of [1] are printed as images, so the
arithmetic behind the FY2025-26 quarterly figure was not read, only the terms
the formula uses.

### Sources that could not be reached

- `ato.gov.au` returned HTTP 403 to every automated fetch on 23 August 2026, so
  the published maximum contribution base and basic concessional contributions
  cap figures were not read at source, and neither was SGR 2009/2.
- `austlii.edu.au` returned HTTP 403 as well, so the mirrored copy of SGR 2009/2
  was no help either.
- `legislation.nsw.gov.au` refuses automated fetches in the same way, so the
  Payroll Tax Act 2007 (NSW) text was not read. The payroll tax rate and
  threshold above rest on the Revenue NSW page, which is the administering
  authority's own publication.
## Modelling simplifications

Each simplification below is a deliberate choice, followed by what it costs.

- **Pay is even across the year.** Base pay for a month is a twelfth of the
  annual rate times headcount. Capping the annual pay rate at the maximum
  contribution base therefore stands in for capping cumulative earnings, which
  is only equivalent because pay is even. Real pay that is front-loaded, or that
  includes a large one-off payment, would reach the cap earlier in the year than
  this model shows.

- **The superannuation cap is applied to an annual rate in both years.** That is
  the correct shape for FY2026-27. For FY2025-26 the law used a quarterly base,
  so the comparative applies a cap shape the year did not have. Uneven pay
  across quarters would have been capped differently in reality.

- **Superannuation is charged on base pay only.** Base pay stands in for the
  earnings base, which is ordinary time earnings in FY2025-26 and qualifying
  earnings in FY2026-27. Allowances and bonuses belong in that base and are not
  modelled, so the charge is understated for any workforce paid meaningfully
  above base. The rest of the usual list does not cut that way. Overtime is
  earnings for hours other than ordinary hours of work, which puts it outside
  the s 6 definition of ordinary time earnings in [1] and outside qualifying
  earnings under s 10A(1)(a) of [2], so leaving it out does not understate the
  base [4]. Annual leave loading is in the base unless it is demonstrably
  referable to a lost opportunity to work overtime, so it is conditional at
  best [4]. Salary sacrificed amounts are in the base already: s 15A(2) of [1]
  names them and s 19(1) puts them in the quarterly salary or wages base, and
  paragraph 10A(1)(h) of [2] puts them in qualifying earnings. Omitting salary
  sacrifice does not understate the base either.

- **The payroll tax base is base pay plus superannuation only.** Fringe benefits
  are taxable wages in New South Wales and are not modelled, and neither are
  grants under an employee share scheme. The other two headings need
  qualification. A contractor payment is wages only where the contract is a
  relevant contract under Division 7 of Part 3 of [5], and the exemptions in
  that Division take many payments out again: services ancillary to the supply
  of goods, services of a kind not ordinarily required by the payer where the
  contractor supplies them to the public generally, a contract under which
  services are supplied on no more than 90 days in a financial year, and owner
  drivers. A termination payment is taxable in its components rather than in
  full, and the tax free part of a genuine redundancy payment is exempt. The
  taxable base is understated, by less than the bare list of headings suggests.
  None of the New South Wales provisions in this bullet were read at source.

- **One threshold, claimed centrally, spread by twelfths.** The full $1,200,000
  New South Wales threshold is claimed once for the group by CivilCo in its
  `Corporate` cost centre, as the designated group employer, at one twelfth per
  month. Three costs follow. Cost centre payroll tax is not meaningful on its
  own: with only two administration staff in CivilCo `Corporate`, that cell
  carries a large negative amount every month and only the group total is
  right. The monthly spread ignores the days in each month that a real monthly
  return uses. Nothing stops the threshold credit exceeding group payroll tax if
  wages fall below the threshold, which would report negative payroll tax.

- **New South Wales only.** No interstate wages, so no apportionment of the
  threshold between jurisdictions and no other state's rate.

- **A full year of depreciation in the year of addition.** The charge is that
  year's additions divided by asset life in months, taken in every month
  including the month of addition. Depreciation in the addition year is
  overstated against any pro rata convention.

- **Only the current year's additions are depreciated.** The rule reads
  additions for the same year, so there is no opening asset base and no carry
  forward. FY2026-27 charges nothing for the fleet added in FY2025-26, and total
  depreciation is well below what a real fleet with multi-year lives would
  carry.

- **No GST anywhere.** All amounts are GST exclusive and no net GST position is
  tracked.

- **No fuel tax credits.** Fuel cost is planned litres times a single group
  price, with no credit for off-road diesel and no hedging.

- **No balance sheet and no cash flow.** The model produces a profit and loss
  only, so nothing tests whether the plan is fundable, and depreciation has no
  asset balance behind it.

- **The `Full Year` period is an input slot, not a total.** It holds annual
  rates, bases and additions, and it sits outside the `FY` consolidation so it
  cannot double count. Rules still compute in it, so a calculated line read at
  `Full Year` can return one month of charge or zero rather than a year. Read
  the year from `FY`.

## Sample data, not a maintained rate table

The statutory figures ship as data so the model runs out of the box and the
tests have something to compute. They are not maintained. Rates, thresholds and
indexed bases change at least annually, the FY2026-27 payday super figures are
new law, and one shipped figure already fails to reconcile, as note B records.

To change them, edit `examples/drivers.csv`. One row per `Year`, `Version`,
`Period`, `DriverMeasure`, with `Period` always `Full Year` because these are
annual figures. `model/processes/LoadDrivers.ti` writes them into the `Drivers`
cube, and every rule reads them from there, so no rule file needs touching.

Changing a driver changes every computed cell that depends on it. Recompute and
update any test that pins expected results before committing, and update the
verification date and the status column in the table above.
