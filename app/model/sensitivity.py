"""Tornado and scenario runs, computed live with the engine. No Streamlit."""
from __future__ import annotations

from .engine import JRC_CURVES, OPTION_IDS, run_model
from .inputs import SCENARIO_ID, SCENARIO_NAMES, InputState


def scenario_runs(state: InputState, include: dict, units: dict | None = None, curves: dict = JRC_CURVES) -> dict:
    """Full model result under each hazard scenario (1 Benign, 2 Base, 3 Stress), all else as now.

    Hazard rows with a custom value keep it; only rows that follow the scenario move.
    """
    return {
        code: run_model(state.values({SCENARIO_ID: code}), include, units, curves) for code in SCENARIO_NAMES
    }


def scenario_table(runs: dict) -> list:
    """Factory NPV of every option, and of the package, under the three scenarios."""
    rows = []
    for oid in OPTION_IDS:
        rows.append({
            "id": oid,
            "name": runs[2]["options"][oid]["name"],
            "benign": runs[1]["options"][oid]["npv"],
            "base": runs[2]["options"][oid]["npv"],
            "stress": runs[3]["options"][oid]["npv"],
        })
    rows.append({
        "id": "package",
        "name": "Selected package",
        "benign": runs[1]["portfolio"]["npv"],
        "base": runs[2]["portfolio"]["npv"],
        "stress": runs[3]["portfolio"]["npv"],
    })
    return rows


def tornado(state: InputState, include: dict, ids, units: dict | None = None, curves: dict = JRC_CURVES) -> list:
    """Package NPV with each input at its Low and at its High, everything else as it is now.

    Rows come back sorted by swing, largest first.
    """
    base_npv = run_model(state.values(), include, units, curves)["portfolio"]["npv"]
    rows = []
    for iid in ids:
        low, high = state.low[iid], state.high[iid]
        npv_low = run_model(state.values({iid: low}), include, units, curves)["portfolio"]["npv"]
        npv_high = run_model(state.values({iid: high}), include, units, curves)["portfolio"]["npv"]
        rows.append({
            "id": iid,
            "low": low,
            "high": high,
            "npv_low": npv_low,
            "npv_high": npv_high,
            "swing": abs(npv_high - npv_low),
            "base_npv": base_npv,
        })
    rows.sort(key=lambda r: r["swing"], reverse=True)
    return rows
