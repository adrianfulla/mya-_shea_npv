"""Tornado targets, switching values, scenario robustness and Monte Carlo."""
import time

import numpy as np
import pandas as pd
import pytest

from model import optimizer, robustness, sensitivity
from model.inputs import InputState
from model.robustness import Settings


@pytest.fixture(scope="module")
def reference(wb):
    state = InputState.from_specs(wb.inputs)
    return robustness.optimum(state, curves=wb.curves)[1]


# --- tornado -------------------------------------------------------------------------------

def test_tornado_on_the_default_package_reproduces_the_workbook_snapshot(state, wb):
    rows = {r["id"]: r for r in robustness.tornado(state, wb.sensitivity_ids, package=wb.include, curves=wb.curves)}
    for want in wb.sens_tornado:
        got = rows[want["id"]]
        assert got["npv_low"] == pytest.approx(want["low"], abs=0.01)
        assert got["npv_high"] == pytest.approx(want["high"], abs=0.01)
        assert got["base_npv"] == pytest.approx(260_114.90, abs=0.01)
        assert not got["changes_low"] and not got["changes_high"]
    # and it agrees with the engine-based tornado used before
    old = {r["id"]: r for r in sensitivity.tornado(state, wb.include, wb.sensitivity_ids, wb.units, wb.curves)}
    assert all(rows[i]["swing"] == pytest.approx(old[i]["swing"], abs=1e-6) for i in old)


def test_reoptimising_never_does_worse_than_holding_the_package(state, wb, reference):
    ids = wb.sensitivity_ids
    held = {r["id"]: r for r in robustness.tornado(state, ids, package=reference["code"], curves=wb.curves)}
    free = robustness.tornado(state, ids, curves=wb.curves)
    assert [r["swing"] for r in free] == sorted((r["swing"] for r in free), reverse=True)
    for row in free:
        assert row["base_npv"] == pytest.approx(reference["npv"])
        for side in ("npv_low", "npv_high"):
            assert row[side] >= held[row["id"]][side] - 1e-6
        if not row["changes_low"]:
            assert row["npv_low"] == pytest.approx(held[row["id"]]["npv_low"])
    assert any(r["changes_low"] or r["changes_high"] for r in free)  # some inputs do move the package


def test_tornado_follows_the_objective_and_the_constraints(state, wb):
    settings = Settings("factory + supplier NPV", None, ("2",), ("7d",))
    rows = robustness.tornado(state, ["G01", "H08"], settings, curves=wb.curves)
    best = optimizer.optimize(state.values(), *settings, curves=wb.curves)["best"]
    for row in rows:
        assert row["base_npv"] == pytest.approx(best["npv_with_suppliers"])
        for package in (row["package_low"], row["package_high"]):
            assert "2" in package and "7d" not in package


# --- switching values ----------------------------------------------------------------------

def test_scannable_inputs_and_scan_range(state, wb):
    ids = robustness.scannable(state, wb.inputs)
    by_id = wb.by_id
    assert all(not by_id[i].is_switch and by_id[i].low < by_id[i].high for i in ids)
    assert "G09" not in ids and "G02a" not in ids and "H08" in ids
    assert robustness.scan_range(state, by_id["H08"]) == (25.0, 1650.0)  # 50 less 50%, 1100 plus 50%
    assert robustness.scan_range(state, by_id["G05"]) == (0.35, 1.0)  # shares stop at 100%
    assert robustness.scan_range(state, by_id["P02"])[0] == 0.0


def test_switching_values_are_real_thresholds(state, wb, reference):
    ids = ["H06", "N04", "H05", "E02", "H08", "M02", "E07", "G03", "S03", "E20"]
    rows = robustness.switching_values(state, wb.by_id, ids, curves=wb.curves)
    assert rows and {r["id"] for r in rows} <= set(ids)
    assert not [r for r in rows if r["id"] == "G03"]  # the exchange rate is information only
    for row in rows:
        spec = wb.by_id[row["id"]]
        lo, hi = robustness.scan_range(state, spec)
        assert lo <= row["threshold"] <= hi
        assert (row["threshold"] > row["current"]) == (row["side"] == "above")
        assert row["enters"] or row["leaves"]
        step = 1.0 if robustness.is_whole(spec) else 0.011 * abs(row["threshold"])
        inward = row["threshold"] - step if row["side"] == "above" else row["threshold"] + step
        outward = row["threshold"] + (0 if robustness.is_whole(spec) else step) * (1 if row["side"] == "above" else -1)
        before = robustness.optimum(state, overrides={row["id"]: inward}, curves=wb.curves)[1]
        after = robustness.optimum(state, overrides={row["id"]: outward}, curves=wb.curves)[1]
        assert before["code"] == reference["code"], row["id"]
        assert after["code"] != reference["code"], row["id"]
        assert set(after["ids"]) == (set(reference["ids"]) | set(row["enters"])) - set(row["leaves"])
        assert row["distance"] == pytest.approx(abs(row["threshold"] - row["current"]) / abs(row["current"]))
    ordered = robustness.sort_switching(rows)
    assert [r["distance"] for r in ordered] == sorted(r["distance"] for r in rows)


def test_switching_values_in_slices_equal_one_run(state, wb):
    ids = ["H06", "N04", "V01", "E02", "M02", "C07"]
    whole = robustness.switching_values(state, wb.by_id, ids, curves=wb.curves)
    sliced = [r for part in (ids[:2], ids[2:5], ids[5:]) for r in
              robustness.switching_values(state, wb.by_id, part, curves=wb.curves)]
    assert sliced == whole


def test_switching_values_respect_forced_options(state, wb):
    settings = Settings(forced_in=("10",))
    rows = robustness.switching_values(state, wb.by_id, ["H06", "N04", "H05", "E02"], settings, curves=wb.curves)
    assert all("10" not in r["leaves"] for r in rows)  # insurance cannot drop out when it is forced in


def test_assumption_flags_use_the_five_closest_inputs_per_option():
    def row(iid, distance, leaves, tag):
        return {"id": iid, "distance": distance, "leaves": leaves, "enters": (), "confidence": tag}

    rows = [row(f"X{i}", i / 100, ("8",), "Local field evidence") for i in range(1, 6)]
    rows.append(row("A1", 0.99, ("8",), "Analyst assumption"))  # sixth closest for option 8: ignored
    rows.append(row("A2", 0.5, ("10",), "Analyst assumption"))
    rows.append(row("A3", 0.6, (), "Analyst assumption"))  # pushes nothing out
    flags = robustness.assumption_flags(rows, ("7a", "8", "10"))
    assert list(flags) == ["10"] and [r["id"] for r in flags["10"]] == ["A2"]
    rows.append(row("A4", 0.001, ("8",), "Analyst assumption"))
    assert [r["id"] for r in robustness.assumption_flags(rows, ("8",))["8"]] == ["A4"]


# --- scenario robustness -------------------------------------------------------------------

def test_scenario_robustness(state, wb, reference):
    result = robustness.scenario_robustness(state, curves=wb.curves)
    assert result["active"] == 2 and result["reference"]["code"] == reference["code"]
    for code, row in result["scenarios"].items():
        direct = optimizer.optimize(state.values({"G09": code}), curves=wb.curves)["best"]
        assert row["best"]["code"] == direct["code"]
        assert row["regret"] >= -1e-6
        assert row["regret"] == pytest.approx(row["best"]["objective"] - row["reference"]["objective"])
    assert result["scenarios"][2]["regret"] == pytest.approx(0, abs=1e-6)
    assert result["scenarios"][3]["best"]["objective"] > result["scenarios"][1]["best"]["objective"]
    # held package under Stress is the engine's own number for that package
    held = optimizer.evaluate_package(state.values({"G09": 3}), reference["include"], wb.curves)
    assert result["scenarios"][3]["reference"]["npv"] == pytest.approx(held["npv"])


# --- Monte Carlo ---------------------------------------------------------------------------

def test_sampling_plan(state, wb):
    plan = robustness.sampling_plan(state, wb.inputs)
    by_id = wb.by_id
    assert all(not by_id[i].is_switch for i in plan["ids"])
    assert "G02a" not in plan["ids"]  # Low = Base = High: nothing to draw
    assert (plan["left"] <= plan["mode"]).all() and (plan["mode"] <= plan["right"]).all()
    assert (plan["left"] < plan["right"]).all()
    at = {iid: k for k, iid in enumerate(plan["ids"])}
    k = at["C03"]  # an ordinary input: Low, Base, High
    assert (plan["left"][k], plan["mode"][k], plan["right"][k]) == (800, 1500, 3000)
    k = at["H08"]  # a hazard input in the Base scenario: halfway to Benign and to Stress
    assert (plan["left"][k], plan["mode"][k], plan["right"][k]) == (150, 250, 675)

    state.custom["G09"] = 3
    stress = robustness.sampling_plan(state, wb.inputs)
    assert (stress["left"][k], stress["mode"][k], stress["right"][k]) == (675, 1100, 1100)
    state.custom["G09"] = 1
    benign = robustness.sampling_plan(state, wb.inputs)
    assert (benign["left"][k], benign["mode"][k], benign["right"][k]) == (50, 50, 150)
    state.custom.update({"G09": 2, "H08": 400, "C03": 3500})  # custom values become the mode
    custom = robustness.sampling_plan(state, wb.inputs)
    assert (custom["left"][k], custom["mode"][k], custom["right"][k]) == (50, 400, 1100)
    k = at["C03"]
    assert (custom["left"][k], custom["mode"][k], custom["right"][k]) == (800, 3500, 3500)


def test_draws_are_reproducible_and_inside_their_ranges(state, wb):
    plan = robustness.sampling_plan(state, wb.inputs)
    x = robustness.draw(plan, 400, seed=7)
    assert x.shape == (400, len(plan["ids"]))
    assert (x >= plan["left"] - 0.5).all() and (x <= plan["right"] + 0.5).all()
    exact = ~plan["whole"]
    assert (x[:, exact] >= plan["left"][exact]).all() and (x[:, exact] <= plan["right"][exact]).all()
    assert (x[:, plan["whole"]] == np.rint(x[:, plan["whole"]])).all() and plan["whole"].any()
    assert (robustness.draw(plan, 400, seed=7) == x).all()
    assert not (robustness.draw(plan, 400, seed=8) == x).all()
    column = x[:, plan["ids"].index("C03")]
    assert abs(column.mean() - (800 + 1500 + 3000) / 3) < 60  # triangular mean


def test_monte_carlo_report(state, wb, reference):
    track = {"optimum": reference["code"], "current": optimizer.code_of(wb.include)}
    result = robustness.monte_carlo(state, wb.inputs, n=150, seed=3, track=track, curves=wb.curves)
    assert result["n"] == 150 and result["sampled_inputs"] > 150
    assert all(0 <= share <= 1 for share in result["inclusion"].values())
    assert result["inclusion"]["7d"] > 0.8 and result["inclusion"]["4"] < 0.2
    assert sum(p["count"] for p in result["packages"]) <= 150
    assert result["packages"][0]["count"] >= result["packages"][-1]["count"]
    top = next((p for p in result["packages"] if p["code"] == reference["code"]), None)
    assert result["hold_share"]["optimum"] == pytest.approx((top["count"] if top else 0) / 150)
    for name in track:
        d = result["tracked"][name]
        assert d["p10"] <= d["p50"] <= d["p90"] and 0 <= d["p_negative"] <= 1 and d["values"].size == 150
        assert result["optimal"]["p50"] >= d["p50"] - 1e-6  # the re-optimised package is never worse
    assert result["tracked"]["optimum"]["p50"] > result["tracked"]["current"]["p50"]
    assert len(result["spearman"]) == 15
    assert all(-1 <= r["rho"] <= 1 for r in result["spearman"])
    assert [abs(r["rho"]) for r in result["spearman"]] == sorted((abs(r["rho"]) for r in result["spearman"]),
                                                                  reverse=True)

    again = robustness.monte_carlo(state, wb.inputs, n=150, seed=3, track=track, curves=wb.curves, chunk=7)
    assert again["inclusion"] == result["inclusion"] and again["packages"] == result["packages"]
    assert (again["tracked"]["optimum"]["values"] == result["tracked"]["optimum"]["values"]).all()
    other = robustness.monte_carlo(state, wb.inputs, n=150, seed=4, track=track, curves=wb.curves)
    assert other["tracked"]["optimum"]["p50"] != result["tracked"]["optimum"]["p50"]


def test_each_draw_is_a_real_reoptimisation(state, wb, reference):
    plan = robustness.sampling_plan(state, wb.inputs)
    x = robustness.draw(plan, 12, seed=11)
    settings = Settings("factory + supplier NPV", 120_000.0, (), ("7d",))
    part = robustness.run_draws(state, plan, x, 0, 12, settings, {"ref": reference["code"]}, wb.curves)
    for k in range(12):
        values = dict(state.values(), **dict(zip(plan["ids"], x[k].tolist())))
        direct = optimizer.optimize(values, *settings, curves=wb.curves)["best"]
        assert part["best"][k] == direct["code"]
        assert part["best_value"][k] == pytest.approx(direct["npv_with_suppliers"])
        held = optimizer.evaluate_package(values, reference["include"], wb.curves)
        assert part["tracked"]["ref"][k] == pytest.approx(held["npv_with_suppliers"])


def test_spearman_matches_a_rank_correlation_with_ties():
    rng = np.random.default_rng(1)
    x = np.rint(rng.normal(size=300) * 3)  # many ties
    y = x * 2 + rng.normal(size=300)
    want = np.corrcoef(pd.Series(x).rank(), pd.Series(y).rank())[0, 1]
    assert robustness.spearman(x, y) == pytest.approx(want)
    assert robustness.spearman(np.arange(10.0), np.arange(10.0) ** 3) == pytest.approx(1)
    assert robustness.spearman(np.arange(10.0), -np.arange(10.0)) == pytest.approx(-1)
    assert robustness.spearman(np.ones(10), np.arange(10.0)) == 0


def test_a_thousand_draws_run_in_seconds(state, wb, reference):
    start = time.perf_counter()
    result = robustness.monte_carlo(state, wb.inputs, n=1000, seed=42, track={"optimum": reference["code"]},
                                    curves=wb.curves)
    assert time.perf_counter() - start < 15  # the brief allows about 30 s
    assert 0.3 < result["hold_share"]["optimum"] < 0.8
