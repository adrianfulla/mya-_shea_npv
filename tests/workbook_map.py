"""Maps every calculated workbook cell to the engine value that should equal it.

`expectations()` yields (sheet, cell, engine value). The parity tests compare each one
with the workbook: Excel's cached values for the base case, a LibreOffice recalculation
for the second case.
"""
from openpyxl.utils import get_column_letter

from model.engine import OPTION_IDS, export_share_curve

YEAR_COLS = [get_column_letter(c) for c in range(4, 35)]  # D..AH = years 0..30

SUPPLY_BLOCKS = {"1": 9, "2": 16, "3": 23, "5": 30, "8": 37, "9": 44, "portfolio": 51}  # header rows
ENGINE_BLOCKS = {oid: 7 + 12 * i for i, oid in enumerate(OPTION_IDS)}  # header rows 7, 19, ..., 151
ENGINE_ROWS = ["active", "capex", "supply", "other", "opex", "opflow", "tax", "fcf", "supplier", "supply_active"]
ENGINE_TOTALS = {"capex": "capex_pv", "fcf": "npv", "supplier": "supplier_pv", "supply_active": "supply_pv"}
PORTFOLIO_ROWS = ["active", "capex", "supply", "other", "int_flood_ins", "int_bat_gen", "int_ppa_pv", "opex",
                  "opflow", "tax", "fcf", "supplier"]  # rows 20..31
CALC_ROWS = ["c_margin", "c_VST", "c_kernel", "c_catch", "c_annual", "c_daily", "c_hourly", "c_out_hrs", "c_avg_kw",
             "c_out_prod", "c_out_spoil", "c_out_cost", "c_bat_avoid", "c_gen_avoid", "c_out_pct", "c_D0", "c_D1",
             "c_prop0", "c_prop1", "c_bi0", "c_bi1", "c_floss0", "c_floss1", "c_rec0", "c_rec1", "c_premium",
             "c_flood_cap", "c_pv_cap", "c_bat_kwh", "c_bat_cap", "c_gen_kva", "c_gen_cap", "c_gen_fuel",
             "c_irr_cap", "c_api_share", "c_wax", "c_regen_ha", "c_stove_served", "c_char", "c_mango_yield",
             "c_mango_t", "c_mango_margin", "c_mango_mpt", "c_sub_t"]  # Calc!C4..C47
EXPORT_SHARES = [0, 0.25, 0.5, 0.7, 0.85, 1]  # Value chain!A66:A71


def _years(sheet, row, array):
    for col, value in zip(YEAR_COLS, array):
        yield sheet, f"{col}{row}", float(value)


def expectations(result, state, specs, include):
    values = state.values()
    vc, c, sup = result["value_chain"], result["calc"], result["supply"]
    opts, port, res = result["options"], result["portfolio"], result["results"]
    tl = result["timeline"]

    # Inputs: case and active value
    for s in specs:
        yield "Inputs", f"H{s.row}", state.resolved_case(s.id)
        yield "Inputs", f"I{s.row}", values[s.id]

    # Hazards: scenario values table
    for row, iid in zip(range(17, 23), ["H01", "H02", "H04", "H05", "H06", "H08"]):
        yield "Hazards", f"B{row}", state.low[iid]
        yield "Hazards", f"C{row}", state.base[iid]
        yield "Hazards", f"D{row}", state.high[iid]

    # Calc
    for row, name in enumerate(CALC_ROWS, start=4):
        yield "Calc", f"C{row}", c[name]

    # Value chain
    yield "Value chain", "B4", vc["crude_kg"]
    yield "Value chain", "B5", vc["units"]
    yield "Value chain", "B6", vc["units_amz"]
    yield "Value chain", "B7", vc["fob"]
    for channel, first, total_row in ((vc["amazon"], 11, 29), (vc["domestic"], 34, 42)):
        for row, line in enumerate(channel["lines"], start=first):
            yield "Value chain", f"B{row}", line["usd"]
            yield "Value chain", f"C{row}", line["pct"]
            yield "Value chain", f"D{row}", line["gh_share"]
            yield "Value chain", f"E{row}", line["stays"]
            yield "Value chain", f"F{row}", line["leaves"]
        yield "Value chain", f"B{total_row}", channel["price"]
        yield "Value chain", f"C{total_row}", channel["pct"]
        yield "Value chain", f"E{total_row}", channel["stays"]
        yield "Value chain", f"F{total_row}", channel["leaves"]
        yield "Value chain", f"E{total_row + 1}", channel["share_gh"]
    yield "Value chain", "E31", vc["amazon"]["share_amazon"]
    yield "Value chain", "K5", vc["amazon"]["stays"]
    yield "Value chain", "L5", vc["amazon"]["leaves"]
    yield "Value chain", "K6", vc["domestic"]["stays"]
    yield "Value chain", "L6", vc["domestic"]["leaves"]
    summary = vc["summary"]
    for row, key in zip(range(47, 53), ["price", "stays", "share_gh", "women", "women_share", "brand_margin"]):
        yield "Value chain", f"B{row}", summary[key]["amazon"]
        yield "Value chain", f"C{row}", summary[key]["domestic"]
        yield "Value chain", f"D{row}", summary[key]["weighted"]
    business = vc["business"]
    for row, key in zip(range(56, 62), ["retail_revenue", "retail_stays", "bulk_revenue", "total_revenue",
                                        "revenue_stays", "share_stays"]):
        yield "Value chain", f"B{row}", business[key]
    yield "Value chain", "B62", vc["margin_vc"]
    for row, share in zip(range(66, 72), EXPORT_SHARES):
        yield "Value chain", f"B{row}", float(export_share_curve(vc, share))

    # Supply
    yield from _years("Supply", 6, sup["baseline"]["q"])
    yield from _years("Supply", 7, sup["baseline"]["loss"])
    for key, header in SUPPLY_BLOCKS.items():
        block = sup["portfolio"] if key == "portfolio" else sup["options"][key]
        yield "Supply", f"B{header + 1}", block["d"]
        yield "Supply", f"B{header + 2}", block["reduction"]
        yield from _years("Supply", header + 3, block["new"])
        yield from _years("Supply", header + 4, block["q"])
        yield from _years("Supply", header + 5, block["loss"])
        yield from _years("Supply", header + 6, block["benefit"])

    # Engine
    yield from _years("Engine", 4, tl["tax_rate"])
    yield from _years("Engine", 5, tl["df"])
    for oid, header in ENGINE_BLOCKS.items():
        block = opts[oid]
        yield "Engine", f"C{header}", block["horizon"]
        for offset, name in enumerate(ENGINE_ROWS, start=1):
            yield from _years("Engine", header + offset, block[name])
            if name in ENGINE_TOTALS:
                yield "Engine", f"C{header + offset}", block[ENGINE_TOTALS[name]]

    # Portfolio
    for row, oid in enumerate(OPTION_IDS, start=5):
        yield "Portfolio", f"C{row}", int(bool(include[oid]))
    for row, name in enumerate(PORTFOLIO_ROWS, start=20):
        yield from _years("Portfolio", row, port[name])
        if name != "active":
            yield "Portfolio", f"C{row}", port["pv"][name]
    yield "Portfolio", "F5", port["npv"]
    yield "Portfolio", "F6", port["capex_pv"]
    yield "Portfolio", "F7", port["npv_with_suppliers"]

    # Results
    for row, r in enumerate(res["options"], start=5):
        yield "Results", f"C{row}", r["npv"]
        yield "Results", f"D{row}", r["capex_pv"]
        yield "Results", f"E{row}", r["supplier_pv"]
        yield "Results", f"F{row}", r["npv_with_suppliers"]
        yield "Results", f"G{row}", r["supply_pv"]
        yield "Results", f"H{row}", r["breakeven"]
        yield "Results", f"I{row}", r["rank"]
        yield "Results", f"J{row}", r["reading"]
    yield "Results", "C19", res["portfolio_npv"]
    yield "Results", "F19", res["portfolio_npv_with_suppliers"]
    for row, key in zip(range(23, 35), ["c_margin", "c_VST", "baseline_supply_loss_y1", "c_catch", "c_out_cost",
                                        "c_out_pct", "c_floss0", "expected_flood_loss", "share_gh_weighted",
                                        "share_revenue_gh", "units", "sales"]):
        yield "Results", f"C{row}", res["key"][key]

    # Checks
    for row, check in enumerate(result["checks"], start=4):
        yield "Checks", f"B{row}", check["value"]
        yield "Checks", f"C{row}", check["status"]


# Formula cells that hold display text only, not model logic.
TEXT_ONLY_FORMULAS = {("Results", "A2")} | {("Hazards", f"A{r}") for r in range(17, 23)}
