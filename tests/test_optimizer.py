"""The optimizer reproduces the Portfolio sheet for every package, and finds the best one."""
import time

import numpy as np
import pytest

from model import engine, optimizer
from model.inputs import InputState
from model.optimizer import MASKS, N_PACKAGES, OBJECTIVES, code_of, ids_of, include_of

ROWS = ["active", "capex", "supply", "other", "int_flood_ins", "int_bat_gen", "int_ppa_pv", "opex", "opflow", "tax",
        "fcf", "supplier"]


def reference(values, include, wb):
    """The engine's Portfolio sheet, already checked cell by cell against the workbook."""
    return engine.run_model(values, include, wb.units, wb.curves)["portfolio"]


def input_sets(wb):
    """Base case plus cases that exercise every switch and both tails of the inputs."""
    state = InputState.from_specs(wb.inputs)
    sets = {"base": state.values(), "benign": state.values({"G09": 1}), "stress": state.values({"G09": 3})}
    sets["switches"] = state.values({"N05": 1, "G08": 1, "C11": 1, "M10": 1, "H09": 2, "P02": 0.5, "G05": 0.4})
    sets["all low"] = {s.id: (state.values()[s.id] if s.is_switch else s.low) for s in wb.inputs}
    sets["all high"] = {s.id: (state.values()[s.id] if s.is_switch else s.high) for s in wb.inputs}
    sets["short horizon"] = state.values({"G02a": 12, "G04a": 3, "E26": 15, "S03": 4, "I10": 5})
    return sets


def test_default_package_is_the_workbook_package(wb, base):
    values = InputState.from_specs(wb.inputs).values()
    result = optimizer.evaluate_package(values, wb.include)
    assert result["npv"] == pytest.approx(260_114.90, abs=0.01)
    assert result["npv_with_suppliers"] == pytest.approx(363_769.33, abs=0.01)
    assert result["capex_pv"] == pytest.approx(239_728.96, abs=0.01)
    assert result["capex_year0"] == pytest.approx(191_192.80, abs=0.01)  # Portfolio!D21
    assert result["ids"] == ("2", "5", "7a", "7d", "8", "10")
    port = base["portfolio"]
    for row in ROWS:  # the annual cash-flow table, row for row
        assert result["cashflows"][row] == pytest.approx(port[row], rel=1e-12, abs=1e-9), row
    assert result["capex_year1"] == pytest.approx(port["capex"][1])


def test_fifty_random_packages_match_evaluate_package_and_the_engine(wb):
    """The brute-force check: the vectorised search, the single-package evaluation and the
    engine's Portfolio sheet give the same numbers for 50 random packages."""
    values = InputState.from_specs(wb.inputs).values()
    pre = optimizer.precompute(values, wb.curves)
    enum = optimizer.enumerate_packages(pre)
    rng = np.random.default_rng(20261005)
    for code in rng.choice(N_PACKAGES, size=50, replace=False):
        include = include_of(int(code))
        one = optimizer.evaluate_package(pre, include)
        port = reference(values, include, wb)
        assert enum.npv[code] == pytest.approx(one["npv"], abs=1e-6)
        assert enum.supplier_pv[code] == pytest.approx(one["supplier_pv"], abs=1e-6)
        assert enum.capex_pv[code] == pytest.approx(one["capex_pv"], abs=1e-6)
        assert enum.capex_year0[code] == pytest.approx(one["capex_year0"], abs=1e-6)
        assert one["npv"] == pytest.approx(port["npv"], abs=1e-6)
        assert one["npv_with_suppliers"] == pytest.approx(port["npv_with_suppliers"], abs=1e-6)
        assert one["capex_pv"] == pytest.approx(port["capex_pv"], abs=1e-6)


def test_all_8192_packages_match_the_engine_at_base(wb):
    values = InputState.from_specs(wb.inputs).values()
    enum = optimizer.enumerate_packages(values, wb.curves)
    worst = 0.0
    for code in range(N_PACKAGES):
        port = reference(values, include_of(code), wb)
        worst = max(worst, abs(enum.npv[code] - port["npv"]),
                    abs(enum.npv[code] + enum.supplier_pv[code] - port["npv_with_suppliers"]),
                    abs(enum.capex_pv[code] - port["capex_pv"]))
    assert worst < 1e-6


def test_random_packages_match_the_engine_under_other_inputs(wb):
    rng = np.random.default_rng(7)
    for name, values in input_sets(wb).items():
        pre = optimizer.precompute(values, wb.curves)
        enum = optimizer.enumerate_packages(pre)
        for code in rng.choice(N_PACKAGES, size=40, replace=False):
            include = include_of(int(code))
            port = reference(values, include, wb)
            one = optimizer.evaluate_package(pre, include)
            assert enum.npv[code] == pytest.approx(port["npv"], abs=1e-6), (name, code)
            assert enum.supplier_pv[code] == pytest.approx(port["pv"]["supplier"], abs=1e-6), (name, code)
            assert enum.capex_pv[code] == pytest.approx(port["capex_pv"], abs=1e-6), (name, code)
            for row in ROWS:
                assert one["cashflows"][row] == pytest.approx(port[row], rel=1e-12, abs=1e-9), (name, code, row)


def test_optimum_beats_the_default_package_and_every_other_package(wb):
    values = InputState.from_specs(wb.inputs).values()
    result = optimizer.optimize(values, "factory NPV", curves=wb.curves)
    best = result["best"]
    assert result["feasible"] and result["n_feasible"] == N_PACKAGES
    assert best["npv"] >= 260_114.90
    # brute force with the engine itself
    brute = max(reference(values, include_of(code), wb)["npv"] for code in range(N_PACKAGES))
    assert best["npv"] == pytest.approx(brute, abs=1e-6)
    assert reference(values, best["include"], wb)["npv"] == pytest.approx(best["npv"], abs=1e-6)


def test_top_ten_is_ordered_distinct_and_led_by_the_optimum(wb):
    values = InputState.from_specs(wb.inputs).values()
    for objective in OBJECTIVES:
        result = optimizer.optimize(values, objective, curves=wb.curves)
        top = result["top"]
        assert len(top) == 10 and top[0] == result["best"]
        scores = [p["objective"] for p in top]
        assert scores == sorted(scores, reverse=True)
        assert len({p["code"] for p in top}) == 10
        for package in top:
            key = "npv" if objective == OBJECTIVES[0] else "npv_with_suppliers"
            assert package["objective"] == pytest.approx(package[key])
    supplier = optimizer.optimize(values, OBJECTIVES[1], curves=wb.curves)["best"]
    factory = optimizer.optimize(values, OBJECTIVES[0], curves=wb.curves)["best"]
    assert supplier["npv_with_suppliers"] >= factory["npv_with_suppliers"]
    with pytest.raises(ValueError):
        optimizer.optimize(values, "revenue", curves=wb.curves)


def test_options_with_no_effect_do_not_pad_the_ranking(wb):
    """The PPA on top of owned PV changes nothing (the interaction row cancels it), so the
    package with both never outranks or duplicates the leaner one."""
    values = InputState.from_specs(wb.inputs).values()
    enum = optimizer.enumerate_packages(values, wb.curves)
    lean, padded = code_of(["7a", "7d"]), code_of(["7a", "7c", "7d"])
    assert enum.npv[lean] == pytest.approx(enum.npv[padded], abs=1e-6)
    for package in optimizer.optimize(enum, "factory NPV", top=40)["top"]:
        assert not {"7a", "7c"} <= set(package["ids"])
    forced = optimizer.optimize(enum, "factory NPV", forced_in=("7c",))["best"]
    assert "7c" in forced["ids"]


def test_budget_and_forced_options_are_respected(wb):
    values = InputState.from_specs(wb.inputs).values()
    enum = optimizer.enumerate_packages(values, wb.curves)
    free = optimizer.optimize(enum, "factory NPV")["best"]
    for budget in (0.0, 20_000.0, 60_000.0, 150_000.0):
        best = optimizer.optimize(enum, "factory NPV", budget=budget)["best"]
        assert best["capex_pv"] <= budget + 1e-6
        assert best["npv"] <= free["npv"] + 1e-6
        affordable = enum.capex_pv <= budget + 1e-6
        assert best["npv"] == pytest.approx(enum.npv[affordable].max(), abs=1e-6)
    zero = optimizer.optimize(enum, "factory NPV", budget=0.0)["best"]
    assert zero["capex_pv"] == 0 and zero["npv"] > 0  # options with no capex still pay

    forced = optimizer.optimize(enum, "factory NPV", forced_in=("1", "6"), forced_out=("7d",))
    assert {"1", "6"} <= set(forced["best"]["ids"]) and "7d" not in forced["best"]["ids"]
    assert all({"1", "6"} <= set(p["ids"]) and "7d" not in p["ids"] for p in forced["top"])
    assert forced["n_feasible"] == N_PACKAGES // 8
    assert forced["best"]["npv"] < free["npv"]

    impossible = optimizer.optimize(enum, "factory NPV", budget=1_000.0, forced_in=("6",))
    assert not impossible["feasible"] and impossible["best"] is None and impossible["top"] == []
    with pytest.raises(ValueError):
        optimizer.optimize(enum, "factory NPV", forced_in=("6",), forced_out=("6",))


def test_frontier_runs_25_levels_up_to_the_unconstrained_optimum(wb):
    values = InputState.from_specs(wb.inputs).values()
    enum = optimizer.enumerate_packages(values, wb.curves)
    free = optimizer.optimize(enum, "factory NPV")["best"]
    rows = optimizer.frontier(enum, "factory NPV", levels=25)
    assert len(rows) == 25
    assert rows[0]["budget"] == 0 and rows[-1]["budget"] == pytest.approx(free["capex_pv"])
    assert rows[-1]["package"]["code"] == free["code"]
    scores = [r["package"]["objective"] for r in rows]
    assert all(b >= a - 1e-6 for a, b in zip(scores, scores[1:]))  # more budget never hurts
    previous = ()
    for row in rows:
        assert row["package"]["capex_pv"] <= row["budget"] + 1e-6
        assert set(previous) - set(row["left"]) | set(row["entered"]) == set(row["package"]["ids"])
        previous = row["package"]["ids"]
    assert any(r["entered"] for r in rows[1:])


def test_codes_round_trip():
    assert MASKS.shape == (8192, 13)
    for code in (0, 1, 4097, 8191):
        assert code_of(include_of(code)) == code and code_of(ids_of(code)) == code
    assert ids_of(code_of({"7a": True, "10": True, "2": False})) == ("7a", "10")


def test_full_search_takes_well_under_a_second(wb):
    values = InputState.from_specs(wb.inputs).values()
    optimizer.optimize(values, "factory NPV", curves=wb.curves)  # warm up
    start = time.perf_counter()
    for _ in range(20):
        optimizer.optimize(values, "factory + supplier NPV", budget=150_000.0, forced_out=("4",), curves=wb.curves)
    assert (time.perf_counter() - start) / 20 < 0.25
