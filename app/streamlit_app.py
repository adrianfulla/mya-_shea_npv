"""Streamlit UI for the Shea Resilience NPV model.

UI only: every number shown comes from model/engine.py, which reproduces the workbook.
Run with:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import streamlit as st

import charts
from charts import ARROW, SHORT, number, pct, usd, usd_short
from model import engine, sensitivity
from model.engine import OPTION_IDS
from model.inputs import (CASE_LABELS, DEFAULT_WORKBOOK, SCENARIO_NAMES, InputState, apply_table_edits,
                          load_workbook_data)

st.set_page_config(page_title="Shea Resilience NPV", layout="wide")

# `use_container_width` was replaced by `width="stretch"` in later Streamlit releases.
_ST_VERSION = tuple(int(p) for p in st.__version__.split(".")[:2])
WIDE = {"width": "stretch"} if _ST_VERSION >= (1, 50) else {"use_container_width": True}

SWITCH_LABELS = {
    "G09": {1: "Benign", 2: "Base", 3: "Stress"},
    "C11": {1: "Bulk benchmark", 2: "Value-chain blend"},
    "M10": {1: "Own screw press", 2: "Solvent toll extraction"},
    "H09": {1: "Africa curve", 2: "Global curve"},
}
TOGGLES = ("G08", "N05")

# Inputs each option's own cash flows read (global levers live in the sidebar).
OPTION_INPUTS = {
    "1": ["I03", "I01", "I02", "I08", "I04", "I05", "P01", "I07", "I09", "I10"],
    "2": ["A08", "A01", "A13", "A04", "A02", "A03", "A05", "A09", "A12", "A07", "A06", "A10", "A11", "H03"],
    "3": ["R07", "R01", "R02", "R09", "R10", "R03", "R04a", "R04b", "R05", "R06", "I06", "R08a", "R08b", "R11"],
    "4": ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"],
    "5": ["S05", "S01", "S03", "S06", "S04", "I05", "S02", "S09", "S07", "S10", "S08"],
    "6": ["H05", "H06", "D07", "H07", "D09", "H09", "D02", "D01", "D03", "D05", "D04", "D06", "D08", "C07"],
    "7a": ["E09", "E01", "E02", "E03", "E23", "E22", "E12", "E26", "C08"],
    "7b": ["H08", "H10", "E07", "E06a", "E06b", "E14", "E05", "E13", "E15", "E27", "E24", "E16", "E17", "E18"],
    "7c": ["E09", "E02", "E03", "E08", "E23", "C08"],
    "7d": ["H08", "H10", "E07", "E25", "E19", "E10", "E20", "E21", "E04"],
    "8": ["M10", "M06", "M01a", "M01b", "M02", "M03", "M04", "M11", "M09", "M05", "G07"],
    "9": ["F03", "F01", "F02", "F04", "F05", "F06", "H03"],
    "10": ["N05", "N01", "N02", "N03a", "N03b", "N04", "H05", "H06", "H07"],
}
VALUE_CHAIN_SLIDERS = ["V01", "V22", "V25", "V14", "V12", "V08"]
WHOLE_UNITS = {"years", "year", "count", "months", "days", "units"}


# --- workbook and session state -----------------------------------------------------------------

@st.cache_data(show_spinner="Reading the workbook…")
def load_workbook(path: str, modified: float):
    return load_workbook_data(path)


@st.cache_data(show_spinner=False)
def workbook_base_case(path: str, modified: float) -> dict:
    """Headline numbers at the workbook's own inputs and package, for the KPI deltas."""
    wb = load_workbook(path, modified)
    result = engine.run_model(InputState.from_specs(wb.inputs).values(), wb.include, wb.units, wb.curves)
    return {
        "npv": result["portfolio"]["npv"],
        "npv_with_suppliers": result["portfolio"]["npv_with_suppliers"],
        "share_gh": result["value_chain"]["summary"]["share_gh"]["weighted"],
        "out_pct": result["calc"]["c_out_pct"],
    }


_WB_KEY = (str(DEFAULT_WORKBOOK), DEFAULT_WORKBOOK.stat().st_mtime)
WB = load_workbook(*_WB_KEY)
SPECS = WB.by_id
UNITS = WB.units
ss = st.session_state


def reset_to_base():
    ss.state = InputState.from_specs(WB.inputs)
    ss.include = dict(WB.include)
    ss.details = []
    ss.gen = ss.get("gen", 0) + 1  # new widget keys, so every widget is rebuilt from the state


if ss.get("wb_key") != _WB_KEY:  # first run, or the xlsx changed on disk
    ss.wb_key = _WB_KEY
    ss.flash = []
    reset_to_base()

STATE: InputState = ss.state


# --- input widgets: one source of truth (STATE), widgets are views of it ------------------------

def set_value(iid: str, value: float):
    """Set an input's active value. Going back to what its case gives clears the custom value."""
    state = ss.state
    state.custom.pop(iid, None)
    default = state.values()[iid]
    if abs(value - default) > 1e-9 * max(1.0, abs(default)):
        state.custom[iid] = float(value)
    ss.gen_editor = ss.get("gen_editor", 0) + 1


def _on_slider(iid: str, key: str, scale: float):
    set_value(iid, ss[key] / scale)


def _on_switch(iid: str, key: str):
    set_value(iid, float(ss[key]))


def _on_include(oid: str, key: str):
    ss.include[oid] = bool(ss[key])


def unit_label(spec) -> str:
    if spec.unit.startswith("pts"):
        return "% points" + spec.unit[3:]
    if spec.unit == "/yr":
        return "% chance per year"
    return spec.unit


def _nice_step(x: float) -> float:
    if x <= 0 or not math.isfinite(x):
        return 1.0
    exponent = math.floor(math.log10(x))
    fraction = x / 10**exponent
    nice = 1 if fraction < 1.5 else 2 if fraction < 3.5 else 5 if fraction < 7.5 else 10
    return nice * 10.0**exponent


def _decimals_needed(x: float, cap: int = 4) -> int:
    for decimals in range(cap + 1):
        if abs(round(x, decimals) - x) < 1e-9 * max(1.0, abs(x)):
            return decimals
    return cap


def _slider_setup(spec, current: float, lo=None, hi=None, step=None):
    """Display scale, range, step and format for one input.

    Range: Low to High widened by 20% each side, never below zero for non-negative inputs,
    never above 100% for shares, and always wide enough to hold the current value. The grid
    is anchored on Base, so the slider can always be put back on the workbook's value.
    """
    iid = spec.id
    low, base, high = STATE.low[iid], STATE.base[iid], STATE.high[iid]
    big_money = spec.unit == "USD" and max(low, base, high) >= 100_000
    scale = 100.0 if spec.is_percent else 0.001 if big_money else 1.0
    fixed_range = lo is not None and hi is not None
    non_negative = min(low, base, high) >= 0
    share = spec.unit.startswith("%")
    if not fixed_range:
        lo, hi = min(low, base, high), max(low, base, high)
        pad = 0.2 * (hi - lo) if hi > lo else 0.2 * abs(base) or 1.0
        lo, hi = lo - pad, hi + pad
        if non_negative:
            lo = max(lo, 0.0)
        if share:
            lo, hi = max(lo, 0.0), min(hi, 1.0)
    lo, hi, shown, anchor = lo * scale, hi * scale, current * scale, base * scale
    if step is None:
        step = _nice_step((hi - lo) / 100)
        if spec.unit in WHOLE_UNITS:
            step = max(1.0, step)
    else:
        step = step * scale
    if fixed_range:
        lo = math.floor(round(lo / step, 9)) * step
        hi = math.ceil(round(hi / step, 9)) * step
    else:
        lo = anchor - math.ceil(round((anchor - lo) / step, 9)) * step
        if non_negative and lo < -1e-9:
            lo += step
        hi = lo + math.ceil(round((hi - lo) / step, 9)) * step
        if share and hi > 100.0 + 1e-9:
            hi -= step
    lo, hi = min(lo, shown), max(hi, shown)
    decimals = min(4, max(max(0, -math.floor(math.log10(step) + 1e-9)), _decimals_needed(anchor)))
    if spec.is_percent:
        fmt = f"%.{decimals}f%%"
    elif big_money:
        fmt = "$%dk"
    elif spec.unit.startswith("USD"):
        fmt = f"$%.{decimals}f"
    else:
        fmt = f"%.{decimals}f"
    return scale, round(lo, 9), round(hi, 9), step, fmt


def as_shown(spec, x: float) -> str:
    """An input value as people read it: 13%, 1,800,000, 0.48."""
    text = number(x * spec.display_scale, 4).rstrip("0").rstrip(".")
    return text + ("%" if spec.is_percent else "")


def _help(spec) -> str:
    lbh = " / ".join(as_shown(spec, x) for x in (STATE.low[spec.id], STATE.base[spec.id], STATE.high[spec.id]))
    note = " Follows the hazard scenario unless you move it." if spec.follows_scenario else ""
    return f"Low / Base / High: {lbh}. {spec.confidence}.{note}"


def input_widget(iid: str, where: str, label: str | None = None, lo=None, hi=None, step=None):
    """Slider for a numeric input, radio or toggle for a switch. Writes back through set_value."""
    spec = SPECS[iid]
    current = VALUES[iid]
    key = f"{where}|{iid}|{ss.gen}"
    if iid in TOGGLES:
        if ss.get(key) != bool(current):
            ss[key] = bool(current)
        st.toggle(label or spec.name, key=key, on_change=_on_switch, args=(iid, key), help=_help(spec))
        return
    if iid in SWITCH_LABELS:
        names = SWITCH_LABELS[iid]
        choice = min(max(int(current), min(names)), max(names))
        if ss.get(key) != choice:
            ss[key] = choice
        st.radio(label or spec.name, list(names), format_func=names.get, key=key, horizontal=True,
                 on_change=_on_switch, args=(iid, key), help=_help(spec))
        return
    scale, lo_d, hi_d, step_d, fmt = _slider_setup(spec, current, lo, hi, step)
    shown = float(current * scale)
    if key not in ss or abs(ss[key] - shown) > 1e-9 * max(1.0, abs(shown)):
        ss[key] = shown
    unit = "USD thousand" if fmt == "$%dk" else unit_label(spec)
    st.slider(label or f"{iid} · {spec.name} ({unit})", min_value=float(lo_d), max_value=float(hi_d),
              step=float(step_d), format=fmt, key=key, on_change=_on_slider, args=(iid, key, scale), help=_help(spec))


def md(text: str) -> str:
    """Escape dollar signs: Streamlit markdown would read $...$ as LaTeX."""
    return text.replace("$", "\\$")


def heading(text: str):
    """Chart title: states the finding with its number."""
    st.markdown("##### " + md(text))


def input_grid(ids, where: str, per_row: int = 3):
    """Input widgets in rows, so the grid stays aligned when a label wraps."""
    for start in range(0, len(ids), per_row):
        for column, iid in zip(st.columns(per_row), ids[start:start + per_row]):
            with column:
                input_widget(iid, where)


def chart(fig, key: str):
    st.plotly_chart(fig, key=key, theme="streamlit", config={"displayModeBar": False}, **WIDE)


def table(data, **kwargs):
    """Read-only table. Short tables are sized to show every row without an inner scrollbar."""
    rows = len(data.data) if hasattr(data, "data") else len(data)
    if "height" not in kwargs and rows <= 32:
        kwargs["height"] = 35 * (rows + 1) + 3
    st.dataframe(data, hide_index=True, **WIDE, **kwargs)


# --- model run (every rerun: one base run plus the three scenario runs) -------------------------

VALUES = STATE.values()
RESULT = engine.run_model(VALUES, ss.include, UNITS, WB.curves)
RUNS = sensitivity.scenario_runs(STATE, ss.include, UNITS, WB.curves)
OPTS, PORT, VC, CALC = RESULT["options"], RESULT["portfolio"], RESULT["value_chain"], RESULT["calc"]
ROWS = {r["id"]: r for r in RESULT["results"]["options"]}
BASE_CASE = workbook_base_case(*_WB_KEY)
HORIZON = int(VALUES["G02a"])
SCENARIO = SCENARIO_NAMES[STATE.scenario()]
MOVED = STATE.moved(WB.inputs)
PACKAGE_CHANGED = ss.include != WB.include


# --- sidebar: the main levers -------------------------------------------------------------------

with st.sidebar:
    st.header("Main levers")
    input_widget("G09", "sb", "Hazard scenario")
    input_widget("G05", "sb", "Export share of sales (%)", lo=0.0, hi=1.0, step=0.01)
    input_widget("G01", "sb", "Discount rate, real (%)", lo=0.05, hi=0.25, step=0.005)
    input_widget("C11", "sb", "Margin basis")
    input_widget("C12", "sb", "Share of butter sold as retail jars (%)")
    input_widget("G08", "sb", "Include carbon revenue")
    input_widget("N05", "sb", "Building insurance already in the baseline")
    input_widget("P02", "sb", "Grant co-funding of farm-side programmes (%)")
    st.button("Reset to base", on_click=reset_to_base, help="Back to the workbook's inputs and package.", **WIDE)
    if not MOVED and not PACKAGE_CHANGED:
        st.success("All inputs are at their workbook base values.")
    else:
        parts = []
        if MOVED:
            parts.append(f"{len(MOVED)} input{'s' if len(MOVED) != 1 else ''} moved off base")
        if PACKAGE_CHANGED:
            parts.append("package changed")
        st.warning(" · ".join(parts).capitalize() + ".")
        if MOVED:
            with st.expander("What moved"):
                for iid, was, now in MOVED:
                    st.caption(f"**{iid}** {SPECS[iid].name}: {as_shown(SPECS[iid], was)} {ARROW} "
                               f"{as_shown(SPECS[iid], now)}")
    st.caption("Constant 2026 USD. Source of truth: Shea_Resilience_NPV_Model.xlsx.")


# --- header -------------------------------------------------------------------------------------

st.title("Shea Resilience NPV model")
st.caption(
    f"Scenario: {SCENARIO}  ·  Discount rate {pct(VALUES['G01'])} real  ·  Margin basis: "
    f"{'bulk benchmark' if VALUES['C11'] == 1 else 'value chain blend'}  ·  Carbon "
    f"{'included' if VALUES['G08'] == 1 else 'excluded'}  ·  Export share {pct(VALUES['G05'], 0)}"
)
for message in ss.flash:
    st.warning(message)
ss.flash = []
for message in RESULT["warnings"]:
    st.error(message)

tab_overview, tab_package, tab_options, tab_chain, tab_inputs, tab_sens, tab_checks = st.tabs(
    ["Overview", "Package builder", "Options", "Value chain", "Inputs", "Sensitivity", "Checks and sources"]
)


# --- Overview -----------------------------------------------------------------------------------

def metric_delta(now: float, was: float, text: str):
    return None if abs(now - was) <= 1e-9 * max(1.0, abs(was)) else text.replace(charts.MINUS, "-") + " vs workbook base"


with tab_overview:
    with st.expander("How to read this model"):
        st.markdown(
            f"""
**What it does.** The model compares 13 ways to protect a shea-butter cosmetics factory in Tema, and the
women processors around Tamale who supply it, from climate risks: poor shea harvests, tree loss, floods
and power cuts.

**The main number is NPV (net present value).** It is what an option is worth today: everything it earns
or saves over its life, minus everything it costs. Money in later years counts for less, because money
today can be put to work (here {pct(VALUES['G01'], 0)} less for each year you wait). Above zero, the option
returns more than it costs. Below zero, it costs the factory more than it brings in.

**Two views of each option.** *Factory NPV* counts only the factory's own cash. Some options (irrigation,
apiaries, stoves) also raise suppliers' income; *factory + supplier NPV* adds that in.

**Three futures.** The hazard scenario moves six risk inputs together: Benign (kind), Base (expected) and
Stress (harsh). The thin grey line under each bar runs from the Benign result to the Stress result, so you
can see which options depend on bad weather to pay off.

**A package is not a simple sum.** When options are combined, overlapping benefits are counted once
(for example, a battery adds nothing if a genset already covers outages). The Package builder shows this.

**Value chain.** For each jar sold, the model follows the price down to the factory's margin and marks
how much stays in Ghana and how much leaves (Amazon fees, freight, tariffs).

**How to use it.** Move the levers in the sidebar and every tab recalculates. Pick options in *Package
builder*. Change any of the {len(WB.inputs)} inputs in *Inputs*; each one shows its sources and how well
supported it is. Inputs tagged "Analyst assumption" are the ones to check first.

**Limits.** All values are in constant 2026 US dollars (no inflation). The case is built from market
benchmarks, not company records. The Free Zone pays no corporate tax for {int(VALUES['G04a'])} years, then a
blend of 15% (exports) and 25% (domestic sales).
"""
        )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Package NPV (factory)", usd(PORT["npv"]),
              metric_delta(PORT["npv"], BASE_CASE["npv"], usd_short(PORT["npv"] - BASE_CASE["npv"], plus=True)))
    k2.metric("Package NPV incl. suppliers", usd(PORT["npv_with_suppliers"]),
              metric_delta(PORT["npv_with_suppliers"], BASE_CASE["npv_with_suppliers"],
                           usd_short(PORT["npv_with_suppliers"] - BASE_CASE["npv_with_suppliers"], plus=True)))
    share_now = VC["summary"]["share_gh"]["weighted"]
    k3.metric("Jar price staying in Ghana", pct(share_now),
              metric_delta(share_now, BASE_CASE["share_gh"], f"{(share_now - BASE_CASE['share_gh']) * 100:+.1f} pts"))
    k4.metric("Outage cost, % of sales", pct(CALC["c_out_pct"], 2),
              metric_delta(CALC["c_out_pct"], BASE_CASE["out_pct"],
                           f"{(CALC['c_out_pct'] - BASE_CASE['out_pct']) * 100:+.2f} pts"),
              delta_color="inverse")

    scenario_rows = {r["id"]: r for r in sensitivity.scenario_table(RUNS)}
    bars = [{"label": SHORT[oid], "npv": ROWS[oid]["npv"], "benign": scenario_rows[oid]["benign"],
             "stress": scenario_rows[oid]["stress"], "reading": ROWS[oid]["reading"]} for oid in OPTION_IDS]
    winners = [oid for oid in OPTION_IDS if ROWS[oid]["npv"] >= 0]
    best = max(OPTION_IDS, key=lambda oid: ROWS[oid]["npv"])
    heading(f"{len(winners)} of 13 options add value on factory cash alone; "
            f"{SHORT[best]} leads at {usd_short(ROWS[best]['npv'])} ({SCENARIO} scenario)")
    chart(charts.npv_by_option(bars), "overview_npv")
    st.caption("Bars: factory NPV in the current scenario. Grey line under each bar: the same option from the "
               "Benign scenario (open circle) to the Stress scenario (filled circle), from three live runs.")

    overview = pd.DataFrame([
        {
            "Rank": ROWS[oid]["rank"],
            "Option": ROWS[oid]["name"],
            "Factory NPV": ROWS[oid]["npv"],
            "Benign": scenario_rows[oid]["benign"],
            "Stress": scenario_rows[oid]["stress"],
            "Capex (PV)": ROWS[oid]["capex_pv"],
            "Supplier income (PV)": ROWS[oid]["supplier_pv"],
            "Factory + supplier NPV": ROWS[oid]["npv_with_suppliers"],
            "Break-even multiple": ROWS[oid]["breakeven"] if isinstance(ROWS[oid]["breakeven"], str)
            else f"{ROWS[oid]['breakeven']:.1f}×",
            "Reading": ROWS[oid]["reading"],
        }
        for oid in OPTION_IDS
    ]).sort_values("Rank")
    money_cols = ["Factory NPV", "Benign", "Stress", "Capex (PV)", "Supplier income (PV)", "Factory + supplier NPV"]
    table(overview.style.format({c: usd for c in money_cols}))
    st.caption("Break-even multiple: how many times more a secured tonne of butter must be worth for the option "
               "to break even (approximate; ignores tax after the holiday).")


# --- Package builder ----------------------------------------------------------------------------

with tab_package:
    st.markdown("**Options in the package**")
    for start in range(0, len(OPTION_IDS), 4):
        for column, oid in zip(st.columns(4), OPTION_IDS[start:start + 4]):
            key = f"inc|{oid}|{ss.gen}"
            if ss.get(key) != ss.include[oid]:
                ss[key] = ss.include[oid]
            column.checkbox(SHORT[oid], key=key, on_change=_on_include, args=(oid, key),
                            help=md(f"{OPTS[oid]['name']}. Standalone factory NPV {usd(ROWS[oid]['npv'])}. "
                                    f"{ROWS[oid]['reading']}."))

    steps = [s for s in engine.npv_bridge(OPTS, PORT, RESULT["supply"], RESULT["timeline"])
             if s[3] != "adjustment" or abs(s[2]) >= 0.5]
    selected = [s for s in steps if s[3] == "option"]
    if not selected:
        st.info(md("No option is selected, so the package NPV is $0. Tick options above to build a package."))
    else:
        verb = "adds" if PORT["npv"] >= 0 else "loses"
        heading(f"Package of {len(selected)} option{'s' if len(selected) != 1 else ''} {verb} "
                f"{usd_short(abs(PORT['npv']))} over {HORIZON} years "
                f"({usd_short(PORT['npv_with_suppliers'])} with supplier income)")
        m1, m2, m3 = st.columns(3)
        m1.metric("Package NPV (factory)", usd(PORT["npv"]))
        m2.metric("Capex (PV)", usd(PORT["capex_pv"]))
        m3.metric("Supplier income (PV)", usd(PORT["pv"]["supplier"]))
        chart(charts.package_waterfall(steps), "package_waterfall")
        st.caption("Green and red bars: each option's standalone NPV. Grey bars: what changes when the options "
                   "are combined (overlaps counted once, tax charged on the combined flow, one "
                   f"{HORIZON}-year horizon). Blue bar: the package NPV, as in the workbook's Portfolio sheet.")
        with st.expander("Waterfall as a table"):
            table(pd.DataFrame([{"Step": s[1], "Kind": s[3], "NPV": s[2]} for s in steps]).style.format({"NPV": usd}))

    years = np.arange(engine.N_YEARS)
    interactions = PORT["int_flood_ins"] + PORT["int_bat_gen"] + PORT["int_ppa_pv"]
    cash = pd.DataFrame({
        "Year": years,
        "Within horizon": PORT["active"].astype(int),
        "Capex": PORT["capex"],
        "Supply benefit (combined)": PORT["supply"],
        "Other benefit": PORT["other"],
        "Flood works × insurance": PORT["int_flood_ins"],
        "Battery × genset": PORT["int_bat_gen"],
        "PPA × owned PV": PORT["int_ppa_pv"],
        "Opex": PORT["opex"],
        "Operating flow": PORT["opflow"],
        "Tax": PORT["tax"],
        "Free cash flow": PORT["fcf"],
        "Supplier income": PORT["supplier"],
        "Discount factor": RESULT["timeline"]["df"],
        "PV of free cash flow": PORT["fcf"] * RESULT["timeline"]["df"],
    })
    if selected:
        shown = cash[cash["Year"] <= HORIZON]
        chart(charts.cashflow_chart(shown["Year"].tolist(), (PORT["supply"] + PORT["other"] + interactions)[: HORIZON + 1],
                                    shown["Opex"].tolist(), shown["Capex"].tolist(), shown["Tax"].tolist(),
                                    shown["Free cash flow"].tolist()), "package_cashflow")
    st.markdown("**Annual cash flows of the package (USD, constant 2026)**")
    plain = ["Year", "Within horizon", "Discount factor"]
    table(cash.style.format({c: usd for c in cash.columns if c not in plain} | {"Discount factor": "{:.4f}"}))
    st.caption(f"Free cash flow and supplier income are zero after year {HORIZON}, as in the workbook. "
               "The other rows continue to year 30 because the workbook's formulas do.")
    st.download_button("Download cash flows as CSV", cash.to_csv(index=False).encode("utf-8"),
                       file_name="package_cash_flows.csv", mime="text/csv")


# --- Options ------------------------------------------------------------------------------------

def gate_for(oid: str):
    for gate in WB.gates:
        if oid in gate["option"].split(" ")[0].split("/"):
            return gate
    return None


with tab_options:
    oid = st.selectbox("Option", OPTION_IDS, format_func=lambda o: OPTS[o]["name"], key="option_pick")
    block, row = OPTS[oid], ROWS[oid]
    horizon = int(block["horizon"])
    heading(f"{block['name']}: factory NPV {usd_short(row['npv'])} on {usd_short(row['capex_pv'])} of capex over "
            f"{horizon} years. {row['reading']}.")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Factory NPV", usd(row["npv"]))
    m2.metric("Capex (PV)", usd(row["capex_pv"]))
    m3.metric("Supplier income (PV)", usd(row["supplier_pv"]))
    m4.metric("Break-even multiple", row["breakeven"] if isinstance(row["breakeven"], str) else f"{row['breakeven']:.1f}×",
              help="How many times more a secured tonne must be worth for the option to break even. "
                   "'n/a' means the option has no supply benefit.")
    m5.metric("Rank of 13", f"{row['rank']}")
    span = slice(0, horizon + 1)
    chart(charts.cashflow_chart(list(range(horizon + 1)), (block["supply"] + block["other"])[span], block["opex"][span],
                                block["capex"][span], block["tax"][span], block["fcf"][span]), f"option_cashflow_{oid}")
    with st.expander("Cash flows as a table"):
        flows = pd.DataFrame({
            "Year": list(range(engine.N_YEARS)), "Capex": block["capex"], "Supply benefit": block["supply"],
            "Other benefit": block["other"], "Opex": block["opex"], "Operating flow": block["opflow"],
            "Tax": block["tax"], "Free cash flow": block["fcf"], "Supplier income": block["supplier"],
        })
        table(flows.style.format({c: usd for c in flows.columns if c != "Year"}))

    gate = gate_for(oid)
    if gate:
        st.info(md(f"**Decision gate before scaling ({gate['status']}).** {gate['gate']}. "
                   f"Evidence replaces inputs: {gate['inputs']}."))
    else:
        st.info("**Decision gate.** The workbook's Gates sheet lists no gate for this option.")

    st.markdown("**Inputs for this option**")
    st.caption("Ranges run from Low to High, widened by 20% each side. Moving a hazard input here overrides the "
               "scenario for that input until you reset.")
    input_grid(OPTION_INPUTS[oid], f"opt{oid}")


# --- Value chain --------------------------------------------------------------------------------

with tab_chain:
    input_grid(VALUE_CHAIN_SLIDERS, "vc")

    summary, business = VC["summary"], VC["business"]
    channel_name = st.radio("Channel", ["Amazon US", "Ghana retail"], horizontal=True, key="vc_channel")
    channel = VC["amazon"] if channel_name == "Amazon US" else VC["domestic"]
    if channel_name == "Amazon US":
        heading(f"{pct(channel['share_gh'])} of the {usd(channel['price'], 2)} Amazon price stays in Ghana; "
                f"Amazon's fees, storage and ads take {pct(channel['share_amazon'])}")
    else:
        heading(f"{pct(channel['share_gh'])} of the {usd(channel['price'], 2)} Ghana retail price stays in Ghana")
    chart(charts.value_chain_waterfall(channel), f"vc_waterfall_{channel_name}")
    with st.expander("Cost lines as a table"):
        lines = pd.DataFrame([{"Line": l["label"], "USD per jar": l["usd"], "% of price": l["pct"],
                               "Share in Ghana": l["gh_share"], "Stays in Ghana": l["stays"],
                               "Leaves Ghana": l["leaves"], "Inputs used": l["inputs"]} for l in channel["lines"]])
        table(lines.style.format({"USD per jar": lambda x: usd(x, 2), "Stays in Ghana": lambda x: usd(x, 2),
                                  "Leaves Ghana": lambda x: usd(x, 2), "% of price": pct,
                                  "Share in Ghana": lambda x: pct(x, 0)}))

    left, right = st.columns(2)
    with left:
        heading(f"At {pct(VALUES['G05'], 0)} exports, {pct(summary['share_gh']['weighted'])} of the jar price "
                "stays in Ghana")
        shares = np.linspace(0, 1, 101)
        chart(charts.export_share_line(shares, engine.export_share_curve(VC, shares), VALUES["G05"],
                                       summary["share_gh"]["weighted"]), "vc_export_share")
        st.caption("Every extra point of export share moves value out of Ghana, because Amazon fees, freight "
                   "and tariffs are paid abroad.")
    with right:
        heading(f"{pct(business['share_stays'])} of total revenue ({usd_short(business['total_revenue'])} a year) "
                "stays in Ghana")
        whole = pd.DataFrame([
            ("Retail revenue", usd(business["retail_revenue"])),
            ("Retail revenue staying in Ghana", usd(business["retail_stays"])),
            ("Bulk refined butter revenue (FOB)", usd(business["bulk_revenue"])),
            ("Total revenue", usd(business["total_revenue"])),
            ("Revenue staying in Ghana", usd(business["revenue_stays"])),
            ("Share of total revenue staying in Ghana", pct(business["share_stays"])),
            ("Contribution margin per t crude butter, retail", usd(VC["margin_vc"])),
            ("Retail jars per year", number(VC["units"])),
        ], columns=["Whole business per year", "Value"])
        table(whole)
        per_jar = pd.DataFrame([
            ("Retail price per jar", *(usd(summary["price"][k], 2) for k in ("amazon", "domestic", "weighted"))),
            ("Stays in Ghana per jar", *(usd(summary["stays"][k], 2) for k in ("amazon", "domestic", "weighted"))),
            ("Share of price staying in Ghana", *(pct(summary["share_gh"][k]) for k in ("amazon", "domestic", "weighted"))),
            ("Paid to women processors per jar", *(usd(summary["women"][k], 2) for k in ("amazon", "domestic", "weighted"))),
            ("Women processors' share of price", *(pct(summary["women_share"][k]) for k in ("amazon", "domestic", "weighted"))),
            ("Brand margin per jar", *(usd(summary["brand_margin"][k], 2) for k in ("amazon", "domestic", "weighted"))),
        ], columns=["Per jar", "Amazon", "Ghana retail", "Weighted"])
        table(per_jar)


# --- Inputs -------------------------------------------------------------------------------------

CHIP = {  # evidence strength as an accent ramp: strong, light, neutral
    "Local field evidence": "background-color: rgba(42, 120, 214, 0.45)",
    "Transferable evidence": "background-color: rgba(42, 120, 214, 0.18)",
    "Analyst assumption": "background-color: rgba(137, 135, 129, 0.35)",
}


def _on_edit(key: str, ids: list):
    ss.details, problems = apply_table_edits(ss.state, SPECS, ids, ss[key].get("edited_rows", {}), ss.details)
    ss.flash.extend(problems)
    ss.gen_editor = ss.get("gen_editor", 0) + 1  # rebuild the table from the state


def _restore_csv(upload_key: str):
    upload = ss.get(upload_key)
    if upload is None:
        ss.flash.append("Choose a CSV file first.")
        return
    try:
        state, problems = InputState.from_csv(upload.getvalue().decode("utf-8-sig"), WB.inputs)
    except (ValueError, UnicodeDecodeError) as error:
        ss.flash.append(f"Could not read that file: {error}")
        return
    ss.state = state
    ss.gen += 1
    ss.flash.extend(problems)


with tab_inputs:
    f1, f2, f3 = st.columns(3)
    groups = f1.multiselect("Group", list(dict.fromkeys(s.group for s in WB.inputs)), key="filter_group")
    tags = f2.multiselect("Confidence tag", list(CHIP), key="filter_tag")
    verdicts = f3.multiselect("Verdict", sorted({s.verdict for s in WB.inputs}), key="filter_verdict")
    visible = [s for s in WB.inputs if (not groups or s.group in groups) and (not tags or s.confidence in tags)
               and (not verdicts or s.verdict in verdicts)]
    st.caption(f"{len(visible)} of {len(WB.inputs)} inputs. Edit Low, Base, High, pick the case (L, B, H), or type a "
               "custom value; a custom value wins over the case, and clearing it goes back. Percentages are shown "
               "as %. Hazard rows with case “Scenario” follow the sidebar scenario. Tick Sources to see where a "
               "number comes from.")

    def shown(iid, x):
        return None if x is None else round(x * SPECS[iid].display_scale, 10)

    frame = pd.DataFrame([
        {
            "Sources": s.id in ss.details,
            "ID": s.id,
            "Input": s.name,
            "Unit": unit_label(s),
            "Low": shown(s.id, STATE.low[s.id]),
            "Base": shown(s.id, STATE.base[s.id]),
            "High": shown(s.id, STATE.high[s.id]),
            "Case": CASE_LABELS[STATE.case[s.id]],
            "Custom": shown(s.id, STATE.custom.get(s.id)),
            "Active": shown(s.id, VALUES[s.id]),
            "Confidence": s.confidence,
            "Verdict": s.verdict,
        }
        for s in visible
    ], columns=["Sources", "ID", "Input", "Unit", "Low", "Base", "High", "Case", "Custom", "Active", "Confidence",
                "Verdict"])
    frame["Custom"] = frame["Custom"].astype("float64")
    editor_key = f"editor|{ss.gen}|{ss.get('gen_editor', 0)}|{hash((tuple(groups), tuple(tags), tuple(verdicts)))}"
    st.data_editor(
        frame.style.map(lambda tag: CHIP.get(tag, ""), subset=["Confidence"]),
        key=editor_key,
        hide_index=True,
        height=460,
        disabled=["ID", "Input", "Unit", "Active", "Confidence", "Verdict"],
        column_config={
            "Sources": st.column_config.CheckboxColumn("Sources", width="small",
                                                       help="Show sources, quoted figure, rationale and correction below."),
            "ID": st.column_config.TextColumn("ID", width="small"),
            "Input": st.column_config.TextColumn("Input", width="medium"),
            "Unit": st.column_config.TextColumn("Unit", width="small"),
            "Low": st.column_config.NumberColumn("Low", format="localized"),
            "Base": st.column_config.NumberColumn("Base", format="localized"),
            "High": st.column_config.NumberColumn("High", format="localized"),
            "Case": st.column_config.SelectboxColumn("Case", options=list(CASE_LABELS.values()), required=True, width="small"),
            "Custom": st.column_config.NumberColumn("Custom", format="localized", help="Overrides the case. Clear to go back."),
            "Active": st.column_config.NumberColumn("Active", format="localized", help="The value the model uses."),
        },
        on_change=_on_edit,
        args=(editor_key, [s.id for s in visible]),
        **WIDE,
    )

    for iid in [d for d in ss.details if d in {s.id for s in visible}]:
        spec = SPECS[iid]
        with st.expander(f"{iid} · {spec.name} ({spec.confidence}, {spec.verdict})", expanded=True):
            st.markdown(md(f"**Rationale.** {spec.rationale or 'None given.'}"))
            if spec.correction:
                st.markdown(md(f"**Correction to earlier source.** {spec.correction}"))
            if not spec.sources:
                st.markdown("No source listed: this input is a design choice or a reasoned estimate.")
            for source in spec.sources:
                link = f"[{md(source.label or source.url)}]({source.url})" if source.url else md(source.label)
                quote = md(f" Quoted figure: “{source.quote}”") if source.quote else ""
                st.markdown(f"- {link}.{quote}")

    d1, d2 = st.columns(2)
    with d1:
        st.download_button("Download current inputs as CSV", STATE.to_csv(WB.inputs).encode("utf-8"),
                           file_name="shea_inputs.csv", mime="text/csv")
        st.caption("The CSV stores values as the workbook does (percentages as fractions).")
    with d2:
        upload_key = f"upload|{ss.gen}"
        st.file_uploader("Restore inputs from a CSV downloaded here", type="csv", key=upload_key)
        st.button("Restore inputs from this file", on_click=_restore_csv, args=(upload_key,))


# --- Sensitivity --------------------------------------------------------------------------------

with tab_sens:
    labels = {s.id: f"{s.id} {s.name}" for s in WB.inputs}
    picked = st.multiselect("Inputs in the tornado", [s.id for s in WB.inputs], default=WB.sensitivity_ids,
                            format_func=labels.get, key=f"tornado_pick|{ss.gen}",
                            help="Default: the inputs in the workbook's Sensitivity sheet.")
    if not any(PORT["include"].values()):
        st.info(md("No option is selected, so the package NPV is $0 whatever the inputs. Pick options in Package builder."))
    elif picked:
        tornado = sensitivity.tornado(STATE, ss.include, picked, UNITS, WB.curves)
        top = tornado[0]
        flips = [r for r in tornado if min(r["npv_low"], r["npv_high"]) < 0 <= r["base_npv"]]
        ending = (f"; {len(flips)} input{'s' if len(flips) != 1 else ''} can turn the package negative"
                  if flips else "; the package stays positive across every Low and High") if PORT["npv"] >= 0 else ""
        heading(f"{labels[top['id']]} moves the package NPV most: {usd_short(top['swing'])} between its Low and "
                f"High{ending}")
        chart(charts.tornado_chart(tornado, labels), "tornado")
        st.caption(md(f"Each input is set to its Low and its High with everything else as it is now. The vertical "
                      f"line is the package NPV now ({usd(PORT['npv'])}). Computed live: {2 * len(picked)} engine runs."))
        frame_t = pd.DataFrame([
            {"Input": labels[r["id"]], "NPV at Low": r["npv_low"], "NPV at High": r["npv_high"], "Swing": r["swing"],
             "NPV now": r["base_npv"],
             "Reading": "Package stays positive" if min(r["npv_low"], r["npv_high"]) >= 0 else "Package can turn negative"}
            for r in tornado
        ])
        table(frame_t.style.format({c: usd for c in ("NPV at Low", "NPV at High", "Swing", "NPV now")}))
    else:
        st.info("Pick at least one input to draw the tornado.")

    st.markdown("**Factory NPV of every option under the three hazard scenarios**")
    scen = pd.DataFrame([{"Option": r["name"], "Benign": r["benign"], "Base": r["base"], "Stress": r["stress"],
                          "Stress minus Benign": r["stress"] - r["benign"]}
                         for r in sensitivity.scenario_table(RUNS)])
    table(scen.style.format({c: usd for c in ("Benign", "Base", "Stress", "Stress minus Benign")}))
    st.caption("The scenario moves H01, H02, H04, H05, H06 and H08 together; everything else stays as it is now.")


# --- Checks and sources -------------------------------------------------------------------------

CHECK_FORMATS = [  # one per check, in the workbook's order
    lambda v: pct(v, 2),
    lambda v: usd(v),
    lambda v: f"{number(v)} out of range",
    lambda v: pct(v, 2) + " a year",
    lambda v: usd(v, 2) + " per jar",
    lambda v: usd(v, 2),
    lambda v: number(v) + " kWh",
    lambda v: usd(v),
    lambda v: f"{number(v)} selected",
    lambda v: "on" if v else "off",
]


def _check_value(index: int, check) -> str:
    value = check["value"]
    if isinstance(value, float) and math.isnan(value):
        return "n/a"
    return CHECK_FORMATS[index](value)


def _status_style(status: str) -> str:
    if status == "OK":
        return "background-color: rgba(0, 131, 0, 0.25)"
    if status == "CHECK":
        return "background-color: rgba(230, 103, 103, 0.40)"
    return "background-color: rgba(137, 135, 129, 0.25)"


with tab_checks:
    failed = [c for c in RESULT["checks"] if c["status"] == "CHECK"]
    heading("All model checks pass" if not failed else f"{len(failed)} model check(s) need attention")
    checks_frame = pd.DataFrame([{"Check": c["check"], "Value": _check_value(i, c), "Status": c["status"],
                                  "What it guards against": c["guards"]} for i, c in enumerate(RESULT["checks"])])
    table(checks_frame.style.map(_status_style, subset=["Status"]),
          column_config={"Status": st.column_config.TextColumn("Status", width="medium")})
    if RESULT["warnings"]:
        st.error("Calculation warnings: " + " ".join(RESULT["warnings"]))

    st.markdown("**Inputs that rest on analyst assumptions**")
    assumptions = [s for s in WB.inputs if s.confidence == "Analyst assumption"]
    st.caption(f"{len(assumptions)} of {len(WB.inputs)} inputs are reasoned estimates or design choices rather than "
               "sourced figures. Verdict “Unsupported” marks the ones with no evidence behind them yet.")
    a1, a2 = st.columns(2)
    a_groups = a1.multiselect("Group", sorted({s.group for s in assumptions}), key="assume_group")
    a_verdicts = a2.multiselect("Verdict", sorted({s.verdict for s in assumptions}), key="assume_verdict")
    table(pd.DataFrame([
        {"ID": s.id, "Group": s.group, "Input": s.name, "Unit": unit_label(s), "Verdict": s.verdict,
         "Rationale": s.rationale}
        for s in assumptions if (not a_groups or s.group in a_groups) and (not a_verdicts or s.verdict in a_verdicts)
    ], columns=["ID", "Group", "Input", "Unit", "Verdict", "Rationale"]), height=320)

    st.markdown("**Decision gates: evidence each option must show before scaling**")
    table(pd.DataFrame(WB.gates).rename(columns={"option": "Option", "gate": "Gate before scaling",
                                                 "inputs": "Inputs it replaces", "status": "Status"}))

    st.markdown("**Source audit: errors found in earlier sources**")
    table(pd.DataFrame(WB.audit_problems).rename(columns={"source": "Source", "affects": "Affects",
                                                          "problem": "Problem found and how the model handles it"}))
    st.markdown(f"**Independent spot-check ({len(WB.audit_spotcheck)} sources re-opened)**")
    spot = pd.DataFrame(WB.audit_spotcheck).rename(columns={"claim": "Claim", "text": "Exact text found",
                                                            "verdict": "Verdict", "url": "URL"})
    has_urls = spot["URL"].str.len().gt(0).any()
    table(spot,
          column_config={"Verdict": st.column_config.TextColumn("Verdict", width="small"),
                         "Claim": st.column_config.TextColumn("Claim", width="medium"),
                         "URL": st.column_config.LinkColumn("URL")},
          column_order=["Verdict", "Claim", "Exact text found"] + (["URL"] if has_urls else []))

    st.markdown(f"**All sources ({len(WB.sources)})**")
    table(pd.DataFrame(WB.sources).rename(columns={"source": "Source", "url": "Link", "used_by": "Used by inputs"}),
          column_config={"Link": st.column_config.LinkColumn("Link")}, height=420)
