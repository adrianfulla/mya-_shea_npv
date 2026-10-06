"""The Streamlit app, run headless: it starts clean and its widgets drive the engine."""
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from model import engine
from model.inputs import InputState

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


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
    assert [t.label for t in app.tabs] == ["Overview", "Package builder", "Options", "Value chain", "Inputs",
                                           "Sensitivity", "Checks and sources"]
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
