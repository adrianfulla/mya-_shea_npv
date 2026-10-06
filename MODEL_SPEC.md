# Model spec: workbook → Python mapping

Source of truth: `app/data/Shea_Resilience_NPV_Model.xlsx`. Every formula below was read from the
workbook and translated one-for-one into `app/model/engine.py`. Input IDs (`G01`, `C03`, …) are the
workbook's `in_<ID>` defined names; `c_…` names are the workbook's Calc names. `t` is the year, 0–30
(columns D–AH). `inc_x` is the Portfolio include switch for option x.

Conventions used throughout the workbook and kept in the engine:

- Percentages are stored as fractions.
- "Year 0 is zero": every annual benefit, opex and supply row is wrapped in `IF(t=0, 0, …)`.
- `PV(row) = SUMPRODUCT(row, DF)` over **all** years 0–30 (see "Things to know" at the end).

## 1. Inputs sheet → `model/inputs.py`

| Workbook | Formula | Python |
|---|---|---|
| `Inputs!I` (active value), 189 rows | `=IF(H="L", Low, IF(H="H", High, Base))` | `InputState.values()` |
| `Inputs!H` for H01, H02, H04, H05, H06, H08 | `=CHOOSE(in_G09,"L","B","H")` | case `"S"` (follows scenario), resolved from the active G09 |
| `Inputs!H` for all other rows | typed `L` / `B` / `H` | `InputState.case` |

App-only addition: a *custom value* per input (set by sliders). It replaces the active value, exactly
as typing over column I would in Excel.

## 2. Hazards sheet → `engine.damage_share()`

Depth-damage table `Hazards!A5:C10` (depths 0, 0.5, 1, 1.5, 2, 3 m; Africa and Global industrial).

| Name | Formula | Python |
|---|---|---|
| `c_D0` | curve = `CHOOSE(H09, Africa, Global)`; x = `MIN(MAX(H06,0),3)`; i = `MATCH(x, depth, 1)`; if i is the last point → D(i), else D(i) + (D(i+1)−D(i)) × (x−depth(i)) / (depth(i+1)−depth(i)) | `damage_share(H06, H09)` |
| `c_D1` | same, with `D07` | `damage_share(D07, H09)` |

## 3. Value chain sheet → `engine.value_chain()`

| Cell / name | Formula | Python key |
|---|---|---|
| B4 `c_crude_kg` | `V02/1000 × V03 / (1−V04)` | `crude_kg` |
| B5 `c_units` | `C01 × 1000 × C12 / c_crude_kg` | `units` |
| B6 `c_units_amz` | `c_units × G05` | `units_amz` |
| B7 `c_fob` | `SUM(B11:B15)` (first five cost lines) | `fob` |

Amazon channel, USD per jar (column B) and share staying in Ghana (column D):

| Row | Line | USD per jar | Ghana share |
|---|---|---|---|
| 11 | Crude shea butter | `c_crude_kg × C02` | 1 |
| 12 | Other ingredients | `V05` | `1 − V06` |
| 13 | Packaging | `V07` | `1 − V08` |
| 14 | Factory conversion | `V09` | 1 |
| 15 | Ghana port, handling, documents | `V10` | 1 |
| 16 | Ocean freight | `V12 / V11` | 0 |
| 17 | US duty and tariff | `(V13 + V14) × c_fob` | 0 |
| 18 | US broker, drayage, trucking | `V15` | 0 |
| 19 | AWD storage | `V16 × V18 × V17` | 0 |
| 20 | AWD processing | `V20 / V19` | 0 |
| 21 | AWD transport to FBA | `V21 × V18` | 0 |
| 22 | FBA fulfillment fee | `V22` | 0 |
| 23 | FBA storage | `V23 × V18` | 0 |
| 24 | Referral fee | `IF(V01 ≤ 10, V32, V24) × V01` | 0 |
| 25 | Advertising | `V25 × V01` | 0 |
| 26 | Returns and damages | `V26 × V01` | 0 |
| 27 | Professional plan | `IF(c_units_amz > 0, V31 × 12 / c_units_amz, 0)` | 0 |
| 28 | Brand contribution margin | `V01 − SUM(B11:B27)` | 1 |
| 29 | Retail price | `SUM(B11:B28)`; stays E29 = Σ B×D; leaves F29 = Σ (B − B×D) | |
| E30 `c_gh_share_exp` | `E29 / B29` | `amazon.share_gh` |
| E31 | `(SUM(B19:B25) + B27) / B29` (share captured by Amazon) | `amazon.share_amazon` |

Ghana retail channel:

| Row | Line | USD per jar | Ghana share |
|---|---|---|---|
| 34–37 | Crude butter, other ingredients, packaging, conversion | as rows 11–14 | 1, `1−V06`, `1−V08`, 1 |
| 38 | Domestic distribution | `V30` | 1 |
| 39 | Retailer and distributor margin | `V28 × V27` | 1 |
| 40 | VAT and levies | `V27 × V29 / (1 + V29)` | 1 |
| 41 | Brand contribution margin | `V27 − SUM(B34:B40)` | 1 |
| 42 | Retail price | `SUM(B34:B41)`; E42, F42 as above | |
| E43 `c_gh_share_dom` | `E42 / B42` | `domestic.share_gh` |

Each line also has `% of price` (column C) = USD ÷ channel price, `Stays` (E) = B × D, `Leaves` (F) = B − E.

Summary (columns B Amazon, C Ghana retail, D weighted) and whole business:

| Cell | Formula | Python key |
|---|---|---|
| D47 | `G05 × B29 + (1−G05) × B42` | `summary.price` |
| D48 | `G05 × E29 + (1−G05) × E42` | `summary.stays` |
| D49 | `D48 / D47` (value-weighted share staying in Ghana) | `summary.share_gh` |
| D50 | `G05 × B11 + (1−G05) × B34` | `summary.women` |
| B51, C51, D51 | women per jar ÷ price | `summary.women_share` |
| D52 `c_unit_margin` | `G05 × B28 + (1−G05) × B41` | `summary.brand_margin` |
| B56 | `c_units × D47` | `business.retail_revenue` |
| B57 | `c_units × D48` | `business.retail_stays` |
| B58 | `C01 × (1−C12) × (1−V04) × 1000 × C15` | `business.bulk_revenue` |
| B59 `c_sales` | `B56 + B58` | `business.total_revenue` |
| B60 | `B57 + B58` | `business.revenue_stays` |
| B61 | `B60 / B59` | `business.share_stays` |
| B62 `c_margin_vc` | `D52 / c_crude_kg × 1000` | `margin_vc` |
| B66:B71 | `(x × E29 + (1−x) × E42) / (x × B29 + (1−x) × B42)` for export share x | `export_share_curve(vc, x)` |

## 4. Calc sheet → `engine.calc()`

| Name | Formula |
|---|---|
| `c_margin` | `IF(C11 = 1, C03, C12 × c_margin_vc + (1−C12) × C03)` |
| `c_VST` | `(1−C05) × C04 + C05 × c_margin` |
| `c_kernel` | `C01 / C13` |
| `c_catch` | `c_kernel × 1000 / (R05 × F06)` |
| `c_annual` | `C01 × c_margin` |
| `c_daily` | `c_annual / C14` |
| `c_hourly` | `c_daily / C09` |
| `c_out_hrs` | `H08 × C09/24 × C14/365` |
| `c_avg_kw` | `C08 × 1000 / (C14 × C09)` |
| `c_out_prod` | `c_out_hrs × c_hourly × C10` |
| `c_out_spoil` | `c_out_hrs / H10 × E07` |
| `c_out_cost` | `c_out_prod + c_out_spoil` |
| `c_bat_avoid` | `E14 × (c_out_spoil + c_out_prod × MIN(1, E06a / c_avg_kw))` |
| `c_gen_avoid` | `c_out_spoil + c_out_prod × MIN(1, E25 / c_avg_kw)` |
| `c_out_pct` | `IF(c_sales > 0, c_out_cost / c_sales, 0)` |
| `c_D0`, `c_D1` | see Hazards |
| `c_prop0`, `c_prop1` | `C07 × c_D0`, `C07 × c_D1` |
| `c_bi0`, `c_bi1` | `H07 × c_daily × C10`, `D09 × c_daily × C10` |
| `c_floss0`, `c_floss1` | `c_prop + c_bi` |
| `c_rec0`, `c_rec1` | `N04 × (MAX(0, c_prop − MAX(N03a × c_prop, N03b)) + c_bi)` |
| `c_premium` | `N01 × C07 + N02 × c_annual` |
| `c_flood_cap` | `D02 × D01 × (1 + D03) + D05 × D04 + D06` |
| `c_pv_cap` | `E09 × E01` |
| `c_bat_kwh` | `E06a × E06b / (E16 × E17 × E18)` |
| `c_bat_cap` | `c_bat_kwh × E05 + E13` |
| `c_gen_kva` | `E25 × E19` |
| `c_gen_cap` | `c_gen_kva × E10` |
| `c_gen_fuel` | `c_out_hrs × MIN(E25, c_avg_kw) × E04` |
| `c_irr_cap` | `I03 × (I01 + (1/I02 − 1) × I08)` |
| `c_api_share` | `MIN(1, A08 / A10 × π × A11² × 100 / c_catch)` |
| `c_wax` | `A08 × A04 × A02 × A03` |
| `c_regen_ha` | `R07 / R09` |
| `c_stove_served` | `MIN(1, S05 × S06 / C01)` |
| `c_char` | `B01 × B02` |
| `c_mango_yield` | `IF(M10 = 1, M01a, M01b)` |
| `c_mango_t` | `M06 × c_mango_yield / 1000` |
| `c_mango_margin` | `M06 × c_mango_yield × (M02 − M04) − M06 × M03` |
| `c_mango_mpt` | `IF(c_mango_t > 0, c_mango_margin / c_mango_t, 0)` |
| `c_sub_t` | `MIN(c_mango_t, M05 × H02 × C01) × (1 − G07)` |

## 5. Supply sheet → `engine.supply()`

For every block (year 0 = 0):

- Supply `Q_t = C01 × C06 × (1−d)^t + new_t`
- Loss `= (MAX(0, C01 − Q_t) + H01 × H02 × MIN(C01, Q_t) × (1 − reduction)) × c_VST`
- Benefit `= baseline loss − block loss` (baseline: `d = H04`, reduction 0, no new supply; rows 6–7)

| Block (rows) | Decline rate d (col B) | Poor-year loss reduction (col B) | New supply (t butter) |
|---|---|---|---|
| 1 Irrigation (9–15) | `H04` | `P01 × I05` | 0 |
| 2 Apiaries (16–22) | `H04` | `c_api_share × A07 × H03` | `A06 × c_api_share × baseline Q_t` |
| 3 Regeneration (23–29) | `H04` | 0 | `R07 × R03 × R05/1000 × C13 × MIN(1, MAX(0, (t−R04a)/(R04b−R04a))) × R06` |
| 5 Stoves (30–36) | `H04 − S04` | `c_stove_served × I05` | 0 |
| 8 Mango (37–43) | `H04` | `c_sub_t / (H02 × C01)` | 0 |
| 9 Fire (44–50) | `H04 − F05 × F03` | `F03 × H03 × F04` | 0 |
| Portfolio (51–57) | `H04 − inc5 × S04 − inc9 × F05 × F03` | `1 − (1 − I05 × MAX(inc1 × P01, inc5 × c_stove_served)) × (1 − inc2 × r2) × (1 − inc8 × r8) × (1 − inc9 × r9)` | `inc2 × new2 + inc3 × new3 × MIN(1, R03 + inc1 × I06) / R03` |

## 6. Engine sheet → `engine.option_cashflows()`

Timeline rows:

- Row 4 tax rate `= IF(t ≤ G04a, 0, G05 × G04b + (1−G05) × G04c)`
- Row 5 discount factor `= 1 / (1 + G01)^t`

Every option block has the same row logic:

- Active `= IF(t ≤ horizon, 1, 0)`; horizon `G02a` for all options except option 3 (`G02b`)
- Operating flow `= supply benefit + other benefit − opex`
- Tax `= tax rate × MAX(0, operating flow)`
- Free cash flow `= (operating flow − capex − tax) × active`
- Supplier income row `= (…) × active`; PV-of-supply row `= supply benefit × active`
- Column C: `SUMPRODUCT(row, DF)` for capex, FCF (the NPV), supplier income and supply benefit

`[t>0]` means `IF(t=0, 0, …)`. `MOD(t, n) = 0` uses Excel's `MOD` (`t − n × INT(t/n)`).

| Opt (rows) | Capex | Supply benefit | Other benefit `[t>0]` | Opex `[t>0]` | Supplier income `[t>0]` |
|---|---|---|---|---|---|
| 1 (7–17) | `IF(t=0, c_irr_cap) × (1−P02) + IF(0 < t < G02a and MOD(t, I10)=0, I03 × I09 × (1−P02))` | Supply 1 | 0 | `I07 × I03 × I01` | `I03 × I04` |
| 2 (19–29) | `IF(MOD(t, A13)=0 and t < G02a, A08 × A01 × (1−P02))` | Supply 2 | `c_wax × MAX(0, A09 − A05)` | 0 | `A08 × A04 × A02 × A12 + c_wax × A05` |
| 3 (31–41) | `(IF(t=0, R07 × R01) + IF(t ≤ R10, c_regen_ha × R02 / (R10+1))) × (1−P02)` | Supply 3 | `G08 × c_regen_ha × R03 × R08a × R08b × MIN(1, t/R11)` | 0 | 0 |
| 4 (43–53) | `IF(t=0, B03 × (1−P02))` | 0 | `G08 × c_char × B07 × B06 × (1−B08)` | `c_char × B04 + B01 × B05` | 0 |
| 5 (55–65) | `IF(MOD(t, S03)=0 and t < G02a, S05 × S01 × (1−P02))` | Supply 5 | `G08 × S05 × S10 × S08 × (1−B08)` | 0 | `MIN(C01, S05 × S06) × S09 × S02 × S07` |
| 6 (67–77) | `IF(t=0, c_flood_cap)` | 0 | `H05 × ((c_floss0 − N05 × c_rec0) − (c_floss1 − N05 × c_rec1))` | `D08 × c_flood_cap` | 0 |
| 7a (79–89) | `IF(t=0, c_pv_cap) + IF(t=E26, E12 × c_pv_cap)` | 0 | `MIN(C08 × 1000, E09 × E02 × (1−E23)^(t−1)) × E03` | `E22 × c_pv_cap` | 0 |
| 7b (91–101) | `IF(t=0, c_bat_cap) + IF(t=E27, E15 × c_bat_kwh × E05)` | 0 | `c_bat_avoid` | `E24 × c_bat_cap` | 0 |
| 7c (103–113) | 0 | 0 | `MIN(C08 × 1000, E09 × E02 × (1−E23)^(t−1)) × E03 × E08` | 0 | 0 |
| 7d (115–125) | `IF(t=0 or (MOD(t, E20)=0 and t < G02a), c_gen_cap)` | 0 | `c_gen_avoid` | `c_gen_fuel + E21 × c_gen_cap` | 0 |
| 8 (127–137) | `IF(t=0, M09)` | Supply 8 | `c_mango_margin − H01 × c_sub_t × c_mango_mpt` | `M11` | 0 |
| 9 (139–149) | `IF(t=0, F02 × (1−P02))` | Supply 9 | 0 | `F03 × c_catch × F01 × (1−P02)` | 0 |
| 10 (151–161) | 0 | 0 | `(1−N05) × H05 × c_rec0` | `(1−N05) × c_premium` | 0 |

## 7. Portfolio sheet → `engine.portfolio()`

Include switches `Portfolio!C5:C17`, in order 1, 2, 3, 4, 5, 6, 7a, 7b, 7c, 7d, 8, 9, 10.
Workbook default: 2, 5, 7a, 7d, 8, 10 on.

| Row | Formula |
|---|---|
| 20 Active | `IF(t ≤ G02a, 1, 0)` (20-year horizon for every option, including option 3) |
| 21 Capex | `Σ inc_i × Engine capex_i` |
| 22 Supply benefit, combined | Supply sheet portfolio block (row 57) |
| 23 Other benefit | `Σ inc_i × Engine other benefit_i` |
| 24 Flood works reduce insurance recoveries | `−inc6 × inc10 × (1−N05) × IF(t=0, 0, H05 × (c_rec0 − c_rec1))` |
| 25 Battery redundant when genset is on | `−inc7b × inc7d × Engine other benefit_7b` |
| 26 PPA and owned PV are alternatives | `−inc7a × inc7c × Engine other benefit_7c` |
| 27 Opex | `Σ inc_i × Engine opex_i` |
| 28 Operating flow | `row 22 + 23 + 24 + 25 + 26 − 27` |
| 29 Tax | `tax rate × MAX(0, operating flow)` |
| 30 Free cash flow | `(operating flow − capex − tax) × active` |
| 31 Supplier income | `(Σ inc_i × Engine supplier income_i) × active` |
| F5 `p_npv` | `C30 = SUMPRODUCT(row 30, DF)` |
| F6 | `C21` (capex PV) |
| F7 `p_npv_cons` | `C30 + C31` |

## 8. Results sheet → `engine.results()`

| Column | Formula |
|---|---|
| C Factory NPV | Engine FCF NPV |
| D Capex (PV) | Engine capex PV |
| E Supplier income PV | Engine supplier income PV |
| F Factory + supplier NPV | `C + E` |
| G PV supply benefit | Engine PV of supply benefit |
| H Break-even multiple | `IF(G > 0, IF(C ≥ 0, "already positive", (G − C) / G), "n/a")` |
| I Rank | `RANK(C, C5:C17)` (1 = highest NPV) |
| J Reading | `IF(C ≥ 0, "Adds value on factory cash alone", IF(F ≥ 0, "Positive only with supplier income", "Does not pay at these inputs"))` |
| C25 | Baseline supply loss, year 1 (`Supply!E7`) |
| C30 | Expected flood loss per year `= H05 × c_floss0` |
| C23–C34 (others) | links to Calc and Value chain names |

## 9. Checks sheet → `engine.checks()`

| # | Check | Value | Status |
|---|---|---|---|
| 1 | Outage cost within 0–10% of sales | `c_out_pct` | OK if `0 ≤ v ≤ 0.1` |
| 2 | Insurance recovery ≤ flood loss | `c_rec0 − c_floss0` | OK if `v ≤ 0` |
| 3 | All shares between 0 and 1 | count of inputs whose unit starts with `%` and whose active value is `< 0` or `> 1` | OK if 0 |
| 4 | Decline rate stays ≥ 0 with options | `MIN(d stoves, d fire, d portfolio)` | OK if `v ≥ 0` |
| 5 | Brand margin per Amazon jar is positive | Value chain B28 | OK if `v > 0` |
| 6 | Value chain sums to retail price | `B29 − V01` | OK if `ABS(v) < 0.005` |
| 7 | PV output ≤ factory electricity use | `E09 × E02 − C08 × 1000` | OK if `v ≤ 0` |
| 8 | Battery and genset not both counted | Portfolio C25 | OK if `inc7b × inc7d = 0` or `v < 0` |
| 9 | Portfolio with nothing selected returns zero | `SUM(include switches)` | `"n/a (options selected)"` if `> 0`, else OK |
| 10 | Carbon off in base case | `G08` | OK if 0, else `"NOTE: carbon on"` |

## 10. Read as data only

`Sensitivity` (static snapshot; the app recomputes it live and the tests use it as extra parity
data), `Gates`, `Source audit`, `Sources`, `README`.

## 11. Optimizer → `model/optimizer.py` (no new formulas)

`precompute(values)` runs the engine once (`timeline`, `value_chain`, `calc`, `supply`,
`option_cashflows`) and keeps each option's Engine rows. A package with include flags `inc` is
then the Portfolio sheet as array math:

| Portfolio row | In the optimizer |
|---|---|
| 21 Capex, 23 Other benefit, 27 Opex | `inc @ row` over the 13 option arrays |
| 22 Supply benefit, combined | Supply rows 52-57, evaluated once for each of the 64 combinations of the six switches it reads (options 1, 2, 3, 5, 8, 9) |
| 24 Flood works × insurance | `−inc6 × inc10 × (1−N05) × H05 × (c_rec0 − c_rec1)`, zero in year 0 |
| 25 Battery × genset | `−inc7b × inc7d × other benefit_7b` |
| 26 PPA × owned PV | `−inc7a × inc7c × other benefit_7c` |
| 28-30 Operating flow, Tax, FCF | same formulas, FCF cut at G02a |
| 31 Supplier income | `(inc @ supplier rows) × active` |
| F5, F6, F7 | `npv`, `capex_pv`, `npv_with_suppliers` |

`enumerate_packages` does this for all 8,192 packages at once. Everything except tax is linear
in the include flags, so it reduces to one present value per option, per supply case and per
interaction; only the taxed years are worked year by year. `tests/test_optimizer.py` compares
every package with `engine.run_model`.

`model/robustness.py` only calls the optimizer with different input values: Low and High
(tornado), a scan plus bisection (switching values), the three scenarios, and triangular draws
(Monte Carlo).

## Things to know (workbook behaviour the engine copies on purpose)

1. **PV totals are not cut at the horizon.** Column C "NPV / total" cells are
   `SUMPRODUCT(row, DF)` over all 31 years. Only the FCF, supplier-income and PV-of-supply rows are
   multiplied by the active flag. So capex PV (Results column D, Portfolio C21) and the Portfolio
   subtotals C22–C29 include years after the horizon, and `C28 − C21 − C29 ≠ C30`.
2. **Portfolio uses the 20-year horizon for option 3**, whose own block uses 30 years (the
   workbook README says so).
3. **Standalone and package tax differ.** Tax is charged on positive operating flow per block, so
   an option with negative operating flow shelters other options' tax inside the package but not
   on its own.
4. **Export-share table** (Value chain rows 66–71) keeps the Amazon per-jar costs at the current
   G05; only the channel weights change. The Professional-plan fee per jar would move slightly.
5. **Apiary new supply** is a share of *baseline* supply (Supply row 6), not of the block's own.
6. **Check 9** does not run an empty portfolio; it only reports "n/a" while options are selected.
   `tests/test_checks.py` runs the empty portfolio for real.

App-only guard (not in the workbook): where Excel would return `#DIV/0!` (for example `R04b = R04a`,
or a zero life in a `MOD`), the engine returns 0 for that term and adds a line to
`result["warnings"]`, which the app shows on the Checks tab. No base-range input triggers it.
