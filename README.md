# Shea Resilience NPV: Streamlit app

An interactive version of `Shea_Resilience_NPV_Model.xlsx`. It compares 13 climate-adaptation
investments for a shea-butter cosmetics factory in Tema, Ghana. Move a slider or flip a switch and
every result recalculates: option NPVs, the combined package, the value chain and the charts.

The workbook is the source of truth. The Python engine is a formula-for-formula translation of it,
and the tests compare every calculated cell with the workbook.

## Run it

```bash
pip install -r requirements.txt
```

```bash
streamlit run app/streamlit_app.py
```

Run the tests:

```bash
pytest
```

Tested with Python 3.13 on Streamlit 1.45.1 (pandas 2.2, plotly 5.24) and on Streamlit 1.65.0
(pandas 3.0, plotly 7.1). The second parity case needs LibreOffice (`soffice` on the PATH) to
recalculate a copy of the workbook; without it that case is skipped and says so.

## What is where

```
app/
  streamlit_app.py      UI only: sidebar, seven tabs, widgets
  charts.py             number formatting and Plotly figures
  model/
    inputs.py           loads the workbook; Low/Base/High case and hazard-scenario logic; CSV
    engine.py           the model: pure functions, numpy, no Streamlit
    sensitivity.py      tornado and scenario runs, computed live with the engine
  data/Shea_Resilience_NPV_Model.xlsx
tests/
  workbook_map.py       maps every calculated workbook cell to an engine value
  test_parity.py        engine vs workbook (Excel's cached values, and a LibreOffice recalculation)
  test_checks.py        the ten model checks, package bridge, input state, table edits
  test_app.py           the app run headless: widgets drive the engine, reruns take under a second
MODEL_SPEC.md           every formula translated, with its workbook cell
```

## How the app maps to the Excel sheets

| Excel sheet | Engine function | Where you see it in the app |
|---|---|---|
| Inputs | `inputs.load_workbook_data`, `InputState.values` | Sidebar, **Inputs** tab, sliders on every tab |
| Hazards | `engine.damage_share` | Flood inputs on the **Options** tab (option 6) |
| Value chain | `engine.value_chain`, `engine.export_share_curve` | **Value chain** tab |
| Calc | `engine.calc` | Feeds everything; key quantities appear as KPIs |
| Supply | `engine.supply` | Supply benefits in **Options** and **Package builder** |
| Engine | `engine.option_cashflows` | **Options** tab (cash flows, NPV) |
| Portfolio | `engine.portfolio`, `engine.npv_bridge` | **Package builder** tab |
| Results | `engine.results` | **Overview** tab |
| Checks | `engine.checks` | **Checks and sources** tab |
| Sensitivity | `sensitivity.tornado`, `sensitivity.scenario_table` (live) | **Sensitivity** tab |
| Gates, Source audit, Sources | read as data | **Options** and **Checks and sources** tabs |

`MODEL_SPEC.md` lists each formula and the workbook cell it comes from.

## How the inputs work

Each of the 189 inputs has Low, Base and High values and a case (L, B or H), as in the workbook.
Six hazard inputs (H01, H02, H04, H05, H06, H08) take their case from the hazard scenario (G09).

The app adds one thing: a **custom value**. Sliders write a custom value, which replaces the active
value, the same as typing over column I in Excel. Moving a slider back to its base value removes
it. A custom value on a hazard input overrides the scenario for that input.

**Reset to base** in the sidebar restores the workbook's inputs and package. The sidebar always
says how many inputs have moved off base.

## How to update inputs

There are two ways.

1. **Edit the workbook and restart.** Change Low, Base, High or the case in the Inputs sheet of
   `app/data/Shea_Resilience_NPV_Model.xlsx`, save, and reload the app. The app picks up the new
   file and resets to it. The include switches on the Portfolio sheet set the default package.
   If you change formulas, the engine will not follow: update `engine.py` and `MODEL_SPEC.md`, and
   let `pytest` tell you whether they still agree.
2. **Edit in the app and download a CSV.** Change values on the **Inputs** tab (or with any
   slider), then use *Download current inputs as CSV*. To come back to that set later, upload the
   file under *Restore inputs from a CSV* on the same tab. The CSV stores values as the workbook
   does (percentages as fractions).

Edits made in the app live in your browser session only. They do not change the workbook.

## Parity with the workbook

- **Base case.** The engine is compared with Excel's cached values for all 6,258 calculated cells
  in the Inputs, Hazards, Value chain, Calc, Supply, Engine, Portfolio, Results and Checks sheets.
  A separate test fails if any formula cell in those sheets is missing from the comparison.
- **Second case.** A copy of the workbook with G09 = 3 (Stress), C11 = 1 and G05 = 50% is
  recalculated by headless LibreOffice and compared cell by cell. A companion test confirms that
  LibreOffice reproduces Excel's cached values for the unchanged workbook.
- **Sensitivity snapshot.** The live tornado and scenario runs reproduce the static values on the
  workbook's Sensitivity sheet.

Tolerance is 1e-6 absolute plus 1e-9 relative, tighter than the USD 1 the brief asked for.

## Things to know

- The workbook's Sensitivity sheet lists 23 inputs, not 22; the tornado defaults to those 23.
- The package uses a 20-year horizon for every option, including regeneration (30 years on its
  own), as the workbook does.
- Column "NPV / total" subtotals in the workbook are not cut at the horizon; only free cash flow
  and supplier income are. The app shows the same numbers. See `MODEL_SPEC.md`.
- Where Excel would show `#DIV/0!` (for example when years to first fruit equals years to full
  yield), the app sets that term to zero and shows a warning instead of failing.
- Charts use neutral greys and one accent. Green and red mean good and bad only: NPV sign, and
  value staying in or leaving Ghana. The pair was checked for colour-blind separation, and sign is
  always also shown by position and by a minus sign.
