"""The ten model checks, the package bridge and the input-state logic."""
import math

import pytest

from model import engine
from model.inputs import InputState, apply_table_edits

NONE = {oid: False for oid in engine.OPTION_IDS}


def run(state, wb, include=None):
    return engine.run_model(state.values(), wb.include if include is None else include, wb.units, wb.curves)


def status(result):
    return {c["check"]: c["status"] for c in result["checks"]}


def test_ten_checks_all_pass_at_base(base, cached):
    assert len(base["checks"]) == 10
    for row, check in enumerate(base["checks"], start=4):
        assert check["check"] == cached["Checks"][f"A{row}"].value
        assert check["guards"] == cached["Checks"][f"D{row}"].value
        assert check["status"] == cached["Checks"][f"C{row}"].value
    assert [c["status"] for c in base["checks"]].count("OK") == 9
    assert status(base)["Portfolio with nothing selected returns zero"] == "n/a (options selected)"
    assert base["warnings"] == []


def test_empty_portfolio_really_is_zero(state, wb):
    """The workbook's check 9 only reports "OK" when nothing is selected; here we run it."""
    result = run(state, wb, NONE)
    port = result["portfolio"]
    assert status(result)["Portfolio with nothing selected returns zero"] == "OK"
    assert port["npv"] == 0 and port["npv_with_suppliers"] == 0 and port["capex_pv"] == 0
    for row in ("capex", "supply", "other", "opex", "tax", "fcf", "supplier"):
        assert not port[row].any(), row


def test_outage_check_trips_when_outages_dwarf_sales(state, wb):
    state.custom.update({"H08": 8000, "E07": 20000})
    assert status(run(state, wb))["Outage cost within 0–10% of sales"] == "CHECK"


def test_recovery_check_trips_when_recovery_exceeds_loss(state, wb):
    state.custom["N04"] = 1.5
    result = run(state, wb)
    assert status(result)["Insurance recovery ≤ flood loss"] == "CHECK"
    assert status(result)["All shares between 0 and 1"] == "CHECK"  # N04 is a % input above 100%


def test_share_check_counts_only_percent_units(state, wb):
    state.custom["C05"] = -0.1  # unit "%"
    state.custom["I05"] = 1.5  # unit "pts": not counted, as in the workbook's LEFT(unit, 1) = "%"
    result = run(state, wb)
    check = next(c for c in result["checks"] if c["check"] == "All shares between 0 and 1")
    assert (check["value"], check["status"]) == (1, "CHECK")


def test_decline_check_trips_below_zero(state, wb):
    state.custom["S04"] = 0.02  # larger than H04 = 0.015
    assert status(run(state, wb))["Decline rate stays ≥ 0 with options"] == "CHECK"


def test_amazon_margin_and_price_checks(state, wb):
    state.custom["V22"] = 9.0  # FBA fee wipes out the margin
    result = run(state, wb)
    assert status(result)["Brand margin per Amazon jar is positive"] == "CHECK"
    assert status(result)["Value chain sums to retail price"] == "OK"  # margin is the balancing line


def test_pv_check_trips_when_pv_exceeds_use(state, wb):
    state.custom["E09"] = 200  # 200 kWp x 1480 = 296 MWh > 250 MWh used
    assert status(run(state, wb))["PV output ≤ factory electricity use"] == "CHECK"


def test_battery_and_genset_are_not_double_counted(state, wb):
    include = dict(wb.include, **{"7b": True, "7d": True})
    result = run(state, wb, include)
    port, battery = result["portfolio"], result["options"]["7b"]
    assert status(result)["Battery and genset not both counted"] == "OK"
    assert port["pv"]["int_bat_gen"] == pytest.approx(-float(battery["other"] @ result["timeline"]["df"]))
    assert (port["int_bat_gen"] == -battery["other"]).all()


def test_carbon_note(state, wb):
    state.custom["G08"] = 1
    result = run(state, wb)
    assert status(result)["Carbon off in base case"] == "NOTE: carbon on"
    for oid in ("3", "4", "5"):
        assert result["options"][oid]["other"][1:21].sum() > 0
    state.custom["G08"] = 0
    off = run(state, wb)
    assert all(not off["options"][oid]["other"].any() for oid in ("3", "4", "5"))


def test_insurance_in_baseline(state, wb, base):
    """N05 = 1: option 10 drops out and option 6 saves only the uninsured loss."""
    state.custom["N05"] = 1
    result = run(state, wb)
    assert result["options"]["10"]["npv"] == 0
    assert not result["options"]["10"]["fcf"].any()
    c, v = result["calc"], state.values()
    uninsured = v["H05"] * ((c["c_floss0"] - c["c_rec0"]) - (c["c_floss1"] - c["c_rec1"]))
    assert result["options"]["6"]["other"][1] == pytest.approx(uninsured)
    assert result["options"]["6"]["other"][1] < base["options"]["6"]["other"][1]


@pytest.mark.parametrize("extra", [(), ("1", "3"), ("6",), ("7b", "7c"), ("1", "3", "4", "6", "7b", "7c", "9")])
def test_bridge_adds_up_to_the_package_npv(state, wb, extra):
    include = dict(wb.include, **{oid: True for oid in extra})
    result = run(state, wb, include)
    steps = engine.npv_bridge(result["options"], result["portfolio"], result["supply"], result["timeline"])
    assert steps[-1][3] == "total"
    assert sum(s[2] for s in steps[:-1]) == pytest.approx(result["portfolio"]["npv"], abs=1e-6)
    by_key = {s[0]: s[2] for s in steps}
    if "6" not in extra:
        assert by_key["int_flood_ins"] == 0
    else:
        assert by_key["int_flood_ins"] < 0  # insurance is in the default package
    assert (by_key["int_bat_gen"] < 0) == ("7b" in extra)
    assert (by_key["int_ppa_pv"] < 0) == ("7c" in extra)
    assert (by_key["horizon"] != 0) == ("3" in extra)


def test_scenario_switch_moves_only_the_six_hazard_rows(state, wb):
    base_values = state.values()
    stress = state.values({"G09": 3})
    moved = sorted(k for k in base_values if base_values[k] != stress[k])
    assert moved == ["G09", "H01", "H02", "H04", "H05", "H06", "H08"]
    for iid in ("H01", "H02", "H04", "H05", "H06", "H08"):
        assert stress[iid] == state.high[iid]
        assert state.values({"G09": 1})[iid] == state.low[iid]
    state.custom["H08"] = 400  # a custom value wins over the scenario
    assert state.values({"G09": 3})["H08"] == 400


def test_moved_and_csv_round_trip(state, wb):
    assert state.moved(wb.inputs) == []
    state.custom["G05"] = 0.6
    state.case["C03"] = "H"
    state.base["E07"] = 750.0
    state.custom["G09"] = 3
    assert {m[0] for m in state.moved(wb.inputs)} == {"G05", "C03", "E07", "G09", "H01", "H02", "H04", "H05", "H06",
                                                       "H08"}
    restored, problems = InputState.from_csv(state.to_csv(wb.inputs), wb.inputs)
    assert problems == []
    assert restored.values() == state.values()
    assert (restored.case, restored.custom, restored.base) == (state.case, state.custom, state.base)


def test_csv_reports_problems(wb):
    text = "ID,Input,Unit,Low,Base,High,Case,Custom,Active\nG01,x,%,0.09,0.2,0.17,B,,\nZZ9,x,,1,2,3,B,,\nC03,x,,a,b,c,B,,\n"
    state, problems = InputState.from_csv(text, wb.inputs)
    assert state.values()["G01"] == 0.2
    assert any("ZZ9" in p for p in problems) and any("C03" in p for p in problems)
    assert any("not in the file" in p for p in problems)
    with pytest.raises(ValueError):
        InputState.from_csv("a,b\n1,2\n", wb.inputs)


def test_zero_denominators_warn_instead_of_crashing(state, wb):
    state.custom.update({"R04b": state.values()["R04a"], "I10": 0, "H10": 0})
    result = run(state, wb, {oid: True for oid in engine.OPTION_IDS})
    assert len(result["warnings"]) >= 3
    assert math.isfinite(result["portfolio"]["npv"])


def test_table_edits_update_the_state(state, wb):
    ids = ["G01", "C03", "H08", "E07"]
    edits = {
        0: {"Base": 15, "Sources": True},  # % input: typed as 15, stored as 0.15
        1: {"Case": "H"},
        2: {"Custom": 400},  # hazard row: a custom value beats the scenario
        3: {"Low": 100, "High": 3000, "Case": "L"},
    }
    details, problems = apply_table_edits(state, wb.by_id, ids, edits, [])
    assert (details, problems) == (["G01"], [])
    values = state.values({"G09": 3})
    assert values["G01"] == pytest.approx(0.15)
    assert values["C03"] == wb.by_id["C03"].high
    assert values["H08"] == 400
    assert (values["E07"], state.high["E07"]) == (100, 3000)

    details, problems = apply_table_edits(state, wb.by_id, ids, {2: {"Custom": None}, 0: {"Sources": False}}, details)
    assert (details, problems) == ([], [])
    assert state.values({"G09": 3})["H08"] == wb.by_id["H08"].high


def test_table_edits_reject_bad_cases_and_blanks(state, wb):
    before = state.copy()
    _, problems = apply_table_edits(state, wb.by_id, ["G01", "H05"], {0: {"Case": "Scenario", "Base": None}}, [])
    assert len(problems) == 2
    assert (state.case, state.base) == (before.case, before.base)
    _, problems = apply_table_edits(state, wb.by_id, ["G01", "H05"], {1: {"Case": "L"}}, [])  # hazard rows may leave
    assert problems == [] and state.case["H05"] == "L"
    _, problems = apply_table_edits(state, wb.by_id, ["G01", "H05"], {1: {"Case": "Scenario"}}, [])  # ...and return
    assert problems == [] and state.case["H05"] == "S"
