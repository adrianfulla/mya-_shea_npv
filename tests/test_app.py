"""The Streamlit app, run headless: it starts clean and its widgets drive the engine."""
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from model import engine, optimizer
from model.inputs import InputState

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
SUB_TABS = ["Package", "Budget frontier", "Scenarios", "Switching values", "Monte Carlo"]


def money(text: str) -> float:
    return float(text.replace("−", "-").replace("$", "").replace(",", ""))


def package_npv(at) -> float:
    return money(next(m.value for m in at.metric if m.label == "Package NPV (factory)"))


@pytest.fixture()
def app():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def key(at, where: str, iid: str) -> str:
    return f"{where}|{iid}|{at.session_state['gen']}"


def test_starts_at_the_workbook_base_case(app):
    labels = [t.label for t in app.tabs]
    assert [l for l in labels if l not in SUB_TABS] == ["Overview", "Package builder", "Optimal package", "Options",
                                                         "Value chain", "Inputs", "Sensitivity", "Checks and sources"]
    assert [l for l in labels if l in SUB_TABS] == SUB_TABS
    values = {m.label: m.value for m in app.metric}
    assert values["Package NPV (factory)"] == "$260,115"
    assert values["Package NPV incl. suppliers"] == "$363,769"
    assert values["Jar price staying in Ghana"] == "31.0%"
    assert values["Outage cost, % of sales"] == "0.61%"
    assert [s.value for s in app.success] == ["All inputs are at their workbook base values."]
    assert not app.error and not app.warning


def test_scenario_radio_moves_the_six_hazard_rows(app, wb):
    app.radio(key=key(app, "sb", "G09")).set_value(3).run()
    assert not app.exception
    state = InputState.from_specs(wb.inputs)
    want = engine.run_model(state.values({"G09": 3}), wb.include, wb.units, wb.curves)["portfolio"]["npv"]
    assert package_npv(app) == pytest.approx(want, abs=0.5)
    assert want == pytest.approx(1_265_155.01, abs=0.01)  # the workbook's Sensitivity sheet, Stress
    assert "7 inputs moved off base" in app.warning[0].value


def test_sidebar_sliders_and_toggles_drive_the_engine(app, wb):
    app.slider(key=key(app, "sb", "G05")).set_value(50.0).run()
    app.slider(key=key(app, "sb", "G01")).set_value(10.0).run()
    app.radio(key=key(app, "sb", "C11")).set_value(1).run()
    app.toggle(key=key(app, "sb", "G08")).set_value(True).run()
    assert not app.exception
    state = InputState.from_specs(wb.inputs)
    state.custom.update({"G05": 0.5, "G01": 0.10, "C11": 1.0, "G08": 1.0})
    assert app.session_state["state"].custom == state.custom
    want = engine.run_model(state.values(), wb.include, wb.units, wb.curves)["portfolio"]["npv"]
    assert package_npv(app) == pytest.approx(want, abs=0.5)
    assert "4 inputs moved off base" in app.warning[0].value

    # putting a slider back on its base value clears it
    app.slider(key=key(app, "sb", "G05")).set_value(85.0).run()
    assert "G05" not in app.session_state["state"].custom


def test_package_checkboxes_and_reset(app, wb):
    app.checkbox(key=key(app, "inc", "7b")).set_value(True).run()
    app.checkbox(key=key(app, "inc", "2")).set_value(False).run()
    include = dict(wb.include, **{"7b": True, "2": False})
    assert app.session_state["include"] == include
    values = InputState.from_specs(wb.inputs).values()
    want = engine.run_model(values, include, wb.units, wb.curves)["portfolio"]["npv"]
    assert package_npv(app) == pytest.approx(want, abs=0.5)
    assert "Package changed" in app.warning[0].value

    next(b for b in app.button if b.label == "Reset to base").click().run()
    assert not app.exception
    assert package_npv(app) == pytest.approx(260_114.90, abs=0.5)
    assert app.session_state["include"] == wb.include
    assert [s.value for s in app.success] == ["All inputs are at their workbook base values."]


def test_option_and_value_chain_sliders(app, wb):
    app.selectbox(key="option_pick").set_value("6").run()
    app.slider(key=key(app, "opt6", "H06")).set_value(1.0).run()  # flood depth: overrides the scenario
    app.slider(key=key(app, "vc", "V01")).set_value(19.99).run()
    assert not app.exception
    custom = app.session_state["state"].custom
    assert custom == pytest.approx({"H06": 1.0, "V01": 19.99})
    state = InputState.from_specs(wb.inputs)
    state.custom.update(custom)
    want = engine.run_model(state.values(), wb.include, wb.units, wb.curves)
    shown = {m.label: m.value for m in app.metric}
    assert money(shown["Factory NPV"]) == pytest.approx(want["options"]["6"]["npv"], abs=0.5)
    assert package_npv(app) == pytest.approx(want["portfolio"]["npv"], abs=0.5)


def test_every_option_page_and_empty_package_render(app):
    for oid in engine.OPTION_IDS:
        app.selectbox(key="option_pick").set_value(oid).run()
        assert not app.exception, oid
    for oid in engine.OPTION_IDS:
        box = app.checkbox(key=key(app, "inc", oid))
        if box.value:
            box.set_value(False).run()
    assert not app.exception
    assert package_npv(app) == 0


def test_a_rerun_takes_under_a_second(app):
    app.slider(key=key(app, "sb", "G05")).set_value(60.0).run()  # warm
    start = time.perf_counter()
    app.slider(key=key(app, "sb", "G05")).set_value(70.0).run()
    assert time.perf_counter() - start < 1.0


# --- Optimal package tab -----------------------------------------------------------------------

def summary(at) -> str:
    """The generated summary at the top of the Optimal package tab (markdown dollar escapes removed)."""
    text = next(m.value for m in at.markdown if m.value.startswith(("The best package", "No package")))
    return text.replace("\\$", "$")


def optimum_metrics(at) -> dict:
    """The four headline metrics of the Optimal package tab (the first ones with these labels)."""
    out = {}
    for m in at.metric:
        if m.label in ("Factory NPV", "NPV incl. supplier income", "Upfront capex (year 0)") and m.label not in out:
            out[m.label] = money(m.value)
    return out


def test_optimal_package_at_base(app, wb):
    values = InputState.from_specs(wb.inputs).values()
    best = optimizer.optimize(values, "factory NPV", curves=wb.curves)["best"]
    assert best["ids"] == ("7a", "7d", "8", "10")
    shown = optimum_metrics(app)
    assert shown["Factory NPV"] == pytest.approx(best["npv"], abs=0.5)
    assert shown["NPV incl. supplier income"] == pytest.approx(best["npv_with_suppliers"], abs=0.5)
    assert shown["Upfront capex (year 0)"] == pytest.approx(best["capex_year0"], abs=0.5)
    text = summary(app)
    assert "7a Solar PV + 7d Diesel genset + 8 Mango toll pilot + 10 Insurance" in text
    assert "more than the Package builder selection" in text
    gain = next(m for m in app.metric if m.label == "NPV gain")
    assert money(gain.value.replace("+", "")) == pytest.approx(best["npv"] - 260_114.90, abs=0.5)


def test_use_this_package_button_fills_the_package_builder(app, wb):
    next(b for b in app.button if b.label == "Use this package in Package builder").click().run()
    assert not app.exception
    assert [o for o, on in app.session_state["include"].items() if on] == ["7a", "7d", "8", "10"]
    assert package_npv(app) == pytest.approx(335_957.64, abs=0.5)
    assert "That is the package selected in Package builder." in summary(app)
    assert any("already the best package" in s.value for s in app.success)
    assert next(b for b in app.button if b.label == "Use this package in Package builder").disabled


def test_objective_budget_and_forced_options(app, wb):
    values = InputState.from_specs(wb.inputs).values()
    gen = app.session_state["gen"]
    app.radio(key=f"opt_objective|{gen}").set_value("factory + supplier NPV").run()
    assert "2 Apiaries" in summary(app) and "factory plus supplier NPV" in summary(app)

    app.toggle(key=f"opt_budget_on|{gen}").set_value(True).run()
    assert app.session_state["opt"]["budget"] >= 179_811  # starts at the unconstrained optimum's capex
    app.slider(key=f"opt_budget|{gen}").set_value(50.0).run()
    want = optimizer.optimize(values, "factory + supplier NPV", budget=50_000.0, curves=wb.curves)["best"]
    shown = optimum_metrics(app)
    assert shown["NPV incl. supplier income"] == pytest.approx(want["npv_with_suppliers"], abs=0.5)
    assert want["capex_pv"] <= 50_000 and "capex budget of $50k" in summary(app)

    app.radio(key=f"force|6|{gen}").set_value("in").run()
    assert not app.exception
    assert any("No package meets the capex budget" in e.value for e in app.error)  # flood works cost more than $50k
    assert summary(app).startswith("No package meets")
    app.toggle(key=f"opt_budget_on|{gen}").set_value(False).run()
    app.radio(key=f"force|7d|{gen}").set_value("out").run()
    want = optimizer.optimize(values, "factory + supplier NPV", forced_in=("6",), forced_out=("7d",),
                              curves=wb.curves)["best"]
    assert optimum_metrics(app)["NPV incl. supplier income"] == pytest.approx(want["npv_with_suppliers"], abs=0.5)
    assert "6 Flood drainage forced in" in summary(app) and "7d Diesel genset forced out" in summary(app)

    next(b for b in app.button if b.label == "Reset to base").click().run()
    assert "7a Solar PV + 7d Diesel genset + 8 Mango toll pilot + 10 Insurance" in summary(app)
    assert "forced" not in summary(app)


def test_optimum_follows_the_inputs(app, wb):
    app.radio(key=key(app, "sb", "G09")).set_value(3).run()
    values = InputState.from_specs(wb.inputs).values({"G09": 3})
    want = optimizer.optimize(values, "factory NPV", curves=wb.curves)["best"]
    assert "6" in want["ids"]
    assert optimum_metrics(app)["Factory NPV"] == pytest.approx(want["npv"], abs=0.5)
    assert "6 Flood drainage" in summary(app)


def test_tornado_targets(app):
    for target in ("Optimal package, held fixed", "Re-optimized at each Low and High",
                   "Current Package builder selection"):
        app.radio(key="tornado_target").set_value(target).run()
        assert not app.exception, target


def test_switching_values_and_monte_carlo_run_on_demand(app):
    assert app.session_state["switching"] is None and app.session_state["mc"] is None
    assert "most sensitive" not in summary(app)
    app.button(key="run_switching").click().run()
    assert not app.exception
    rows = app.session_state["switching"]["rows"]
    assert rows and rows[0]["distance"] <= rows[-1]["distance"]
    assert "The decision is most sensitive to" in summary(app)
    assert any("rests on analyst assumptions" in w.value for w in app.warning)

    app.slider(key="mc_n").set_value(200).run()
    app.button(key="run_mc").click().run()
    assert not app.exception
    mc = app.session_state["mc"]
    assert (mc["n"], mc["seed"], mc["result"]["n"]) == (200, 42, 200)
    assert "of 200 Monte Carlo draws" in summary(app)
    labels = {m.label for m in app.metric}
    assert {"P10", "P50 (median)", "P90", "P(NPV < 0)"} <= labels
    assert any("Risks that move together are not captured" in i.value for i in app.info)

    # a slider move makes both results stale: they are hidden, not recomputed
    app.slider(key=key(app, "sb", "G05")).set_value(60.0).run()
    assert "Monte Carlo draws" not in summary(app) and "most sensitive" not in summary(app)
    assert sum("changed since the last run" in i.value for i in app.info) == 2
    app.slider(key=key(app, "sb", "G05")).set_value(85.0).run()  # back to the inputs of the run: shown again
    assert "of 200 Monte Carlo draws" in summary(app) and "most sensitive" in summary(app)
