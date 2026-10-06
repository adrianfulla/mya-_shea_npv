"""How robust is the optimal package? No Streamlit.

Four views, all built on `optimizer` (which calls the engine):

  * tornado on a fixed package, or on the re-optimised NPV;
  * switching values: the input value at which the optimal package changes;
  * scenario robustness: the optimum under Benign, Base and Stress, and the regret of today's;
  * Monte Carlo: independent triangular draws, re-optimised at every draw.

The long runs (switching values, Monte Carlo) work on slices, so a caller can cache each
slice and show progress between them.
"""
from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

from . import optimizer
from .engine import JRC_CURVES, OPTION_IDS
from .inputs import SCENARIO_CASE, SCENARIO_ID, SCENARIO_NAMES, InputState
from .optimizer import OBJECTIVES, SIZES

ASSUMPTION_TAG = "Analyst assumption"
WHOLE_UNITS = {"years", "year", "count", "months", "days", "units"}  # inputs that only make sense as whole numbers


class Settings(NamedTuple):
    """What the optimizer is asked to do."""

    objective: str = OBJECTIVES[0]
    budget: float | None = None
    forced_in: tuple = ()
    forced_out: tuple = ()


def is_whole(spec) -> bool:
    return spec.unit in WHOLE_UNITS


def optimum(state: InputState, settings: Settings = Settings(), overrides: dict | None = None,
            curves: dict = JRC_CURVES):
    """(enumeration, best package or None) at the state's values, with optional one-off overrides."""
    enum = optimizer.enumerate_packages(state.values(overrides), curves)
    return enum, optimizer.best_package(enum, *settings)


def _best_code(state, settings, overrides, curves, forced_ok):
    enum = optimizer.enumerate_packages(state.values(overrides), curves)
    ok = forced_ok if settings.budget is None else forced_ok & (enum.capex_pv <= settings.budget + optimizer.TIE)
    return enum, optimizer.best_code(enum.objective(settings.objective), ok, enum.capex_pv)


def _forced_ok(settings: Settings) -> np.ndarray:
    blank = optimizer.Enumeration(*(np.zeros(optimizer.N_PACKAGES),) * 4)
    return optimizer.feasible(blank, None, settings.forced_in, settings.forced_out)


def _diff(before: tuple, after: tuple) -> tuple:
    return tuple(o for o in after if o not in before), tuple(o for o in before if o not in after)


# --- tornado -------------------------------------------------------------------------------

def tornado(state: InputState, ids, settings: Settings = Settings(), package=None, curves: dict = JRC_CURVES) -> list:
    """The objective with each input at its Low and at its High, everything else as it is now.

    `package` (include dict, ids or code) holds that package fixed. `package=None` re-optimises
    at every Low and High point, within `settings`. Rows are sorted by swing, largest first.
    """
    forced_ok = _forced_ok(settings)
    fixed = None if package is None else (package if isinstance(package, int) else optimizer.code_of(package))

    def point(overrides):
        if fixed is not None:
            enum = optimizer.enumerate_packages(state.values(overrides), curves)
            return float(enum.objective(settings.objective)[fixed]), fixed
        enum, code = _best_code(state, settings, overrides, curves, forced_ok)
        return (float("nan"), None) if code is None else (float(enum.objective(settings.objective)[code]), code)

    base_value, base_code = point(None)
    rows = []
    for iid in ids:
        low, high = state.low[iid], state.high[iid]
        value_low, code_low = point({iid: low})
        value_high, code_high = point({iid: high})
        rows.append({
            "id": iid,
            "low": low,
            "high": high,
            "npv_low": value_low,
            "npv_high": value_high,
            "swing": abs(value_high - value_low),
            "base_npv": base_value,
            "package_low": None if code_low is None else optimizer.ids_of(code_low),
            "package_high": None if code_high is None else optimizer.ids_of(code_high),
            "changes_low": code_low != base_code,
            "changes_high": code_high != base_code,
        })
    rows.sort(key=lambda r: (-(r["swing"] if math.isfinite(r["swing"]) else -1.0)))
    return rows


# --- switching values ----------------------------------------------------------------------

def scannable(state: InputState, specs) -> list:
    """Inputs worth scanning: not a switch, and with a real Low < High range."""
    return [s.id for s in specs if not s.is_switch and state.low[s.id] < state.high[s.id]]


def scan_range(state: InputState, spec) -> tuple:
    """Extended range: from Low less 50% to High plus 50%, kept inside 0-100% for shares."""
    low, high = state.low[spec.id], state.high[spec.id]
    lo, hi = low - 0.5 * abs(low), high + 0.5 * abs(high)
    if spec.is_percent:
        lo, hi = max(lo, 0.0), min(hi, 1.0)
    return lo, hi


def switching_values(state: InputState, by_id: dict, ids, settings: Settings = Settings(), points: int = 15,
                     precision: float = 0.01, curves: dict = JRC_CURVES) -> list:
    """Where does the optimal package change as one input moves?

    Each input is scanned at `points` values across its extended range and the model re-optimised
    at each. For the first change on either side of the current value, the threshold is located
    by bisection to `precision` (relative), or to the whole number for whole-number inputs.
    Returns one row per input and side that flips, unsorted.
    """
    forced_ok = _forced_ok(settings)
    _, reference = _best_code(state, settings, None, curves, forced_ok)
    now = state.values()
    rows = []
    for iid in ids:
        spec = by_id[iid]
        whole = is_whole(spec)
        lo, hi = scan_range(state, spec)
        current = now[iid]
        grid = np.linspace(lo, hi, points)
        if whole:
            grid = np.unique(np.rint(grid))
        seen = {}

        def code_at(x):
            if x not in seen:
                seen[x] = _best_code(state, settings, {iid: x}, curves, forced_ok)[1]
            return seen[x]

        for side, candidates in (("above", [x for x in grid if x > current]),
                                 ("below", [x for x in grid[::-1] if x < current])):
            same = current
            flipped = None
            for x in candidates:
                if code_at(float(x)) != reference:
                    flipped = float(x)
                    break
                same = float(x)
            if flipped is None:
                continue
            floor = 1e-9 * (hi - lo)  # stops the search when the flip sits right at zero
            for _ in range(80):  # bisection between a value that keeps the package and one that changes it
                gap = abs(flipped - same)
                if (whole and gap <= 1) or (not whole and gap <= max(precision * max(abs(flipped), abs(same)), floor)):
                    break
                middle = (same + flipped) / 2
                if whole:
                    middle = float(math.floor(middle) if side == "above" else math.ceil(middle))
                    if middle in (same, flipped):
                        break
                if code_at(middle) != reference:
                    flipped = middle
                else:
                    same = middle
            after = code_at(flipped)
            threshold = flipped if whole else (same + flipped) / 2
            enters, leaves = _diff(optimizer.ids_of(reference) if reference is not None else (),
                                   optimizer.ids_of(after) if after is not None else ())
            distance = abs(threshold - current)
            rows.append({
                "id": iid,
                "side": side,
                "current": current,
                "threshold": threshold,
                "distance": distance / abs(current) if current != 0 else distance / (state.high[iid] - state.low[iid]),
                "distance_basis": "current value" if current != 0 else "Low to High range",
                "enters": enters,
                "leaves": leaves,
                "package_after": optimizer.ids_of(after) if after is not None else None,
                "scan_low": lo,
                "scan_high": hi,
                "inside_low_high": state.low[iid] <= threshold <= state.high[iid],
                "confidence": spec.confidence,
            })
    return rows


def sort_switching(rows: list) -> list:
    """Closest threshold first."""
    return sorted(rows, key=lambda r: (r["distance"], r["id"]))


def assumption_flags(rows: list, package_ids, top: int = 5) -> dict:
    """Options whose place in the package hangs on analyst assumptions.

    For each option in the package: the `top` closest switching-value inputs that push it out.
    The option is flagged if any of them is tagged "Analyst assumption".
    Returns option id -> list of those rows.
    """
    flags = {}
    for oid in package_ids:
        closest = [r for r in sort_switching(rows) if oid in r["leaves"]][:top]
        assumed = [r for r in closest if r["confidence"] == ASSUMPTION_TAG]
        if assumed:
            flags[oid] = assumed
    return flags


# --- scenario robustness -------------------------------------------------------------------

def scenario_robustness(state: InputState, settings: Settings = Settings(), curves: dict = JRC_CURVES) -> dict:
    """The optimum under each hazard scenario, and today's optimum evaluated under each.

    Regret = that scenario's best objective minus what today's optimal package earns there.
    """
    _, reference = optimum(state, settings, None, curves)
    scenarios = {}
    for code, name in SCENARIO_NAMES.items():
        enum, best = optimum(state, settings, {SCENARIO_ID: code}, curves)
        held = None if reference is None else enum.package(reference["code"], settings.objective)
        scenarios[code] = {
            "name": name,
            "best": best,
            "reference": held,
            "regret": None if best is None or held is None else best["objective"] - held["objective"],
        }
    return {"active": state.scenario(), "reference": reference, "scenarios": scenarios}


# --- Monte Carlo ---------------------------------------------------------------------------

def sampling_plan(state: InputState, specs) -> dict:
    """Triangular distribution (left, mode, right) for every non-switch input that can vary.

    The mode is the input's current value: Base, unless its case or a custom value says otherwise.
    Hazard inputs that follow the scenario are drawn inside the active scenario's band: around that
    scenario's value, and no further than halfway to the neighbouring scenario's value.
    """
    now = state.values()
    scenario = state.scenario()
    ids, left, mode, right, whole = [], [], [], [], []
    for s in specs:
        if s.is_switch:
            continue
        low, base, high = state.low[s.id], state.base[s.id], state.high[s.id]
        if state.case[s.id] == SCENARIO_CASE and s.id not in state.custom:
            lo_mid, hi_mid = (low + base) / 2, (base + high) / 2
            a, m, b = ((low, low, lo_mid), (lo_mid, base, hi_mid), (hi_mid, high, high))[scenario - 1]
            a, b = min(a, b), max(a, b)
        else:
            m = now[s.id]
            a, b = min(low, high, m), max(low, high, m)
        if b <= a:
            continue
        ids.append(s.id)
        left.append(a)
        mode.append(min(max(m, a), b))
        right.append(b)
        whole.append(is_whole(s))
    return {"ids": ids, "left": np.array(left), "mode": np.array(mode), "right": np.array(right),
            "whole": np.array(whole, dtype=bool)}


def draw(plan: dict, n: int, seed: int) -> np.ndarray:
    """n independent draws of every sampled input: array [n, inputs]. Same seed, same draws."""
    rng = np.random.default_rng(seed)
    x = rng.triangular(plan["left"], plan["mode"], plan["right"], size=(n, len(plan["ids"])))
    x[:, plan["whole"]] = np.rint(x[:, plan["whole"]])
    return x


def run_draws(state: InputState, plan: dict, x: np.ndarray, start: int, stop: int, settings: Settings = Settings(),
              track: dict | None = None, curves: dict = JRC_CURVES) -> dict:
    """Re-optimise at draws [start, stop). `track`: name -> package code to evaluate at every draw.

    Returns arrays over the slice: the optimal package code and objective, and each tracked
    package's objective.
    """
    track = track or {}
    forced_ok = _forced_ok(settings)
    base_values = state.values()
    ids = plan["ids"]
    count = stop - start
    best = np.full(count, -1, dtype=int)
    best_value = np.full(count, np.nan)
    tracked = {name: np.empty(count) for name in track}
    for k in range(count):
        values = dict(base_values)
        values.update(zip(ids, x[start + k].tolist()))
        enum = optimizer.enumerate_packages(values, curves)
        score = enum.objective(settings.objective)
        ok = forced_ok if settings.budget is None else forced_ok & (enum.capex_pv <= settings.budget + optimizer.TIE)
        code = optimizer.best_code(score, ok, enum.capex_pv)
        if code is not None:
            best[k], best_value[k] = code, score[code]
        for name, tracked_code in track.items():
            tracked[name][k] = score[tracked_code]
    return {"best": best, "best_value": best_value, "tracked": tracked}


def _rank(a: np.ndarray) -> np.ndarray:
    """Ranks with ties sharing their average rank."""
    _, inverse, counts = np.unique(a, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    return (ends - (counts - 1) / 2.0)[inverse]


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation; 0 when either side does not vary."""
    rx, ry = _rank(x), _rank(y)
    sx, sy = rx.std(), ry.std()
    if sx == 0 or sy == 0:
        return 0.0
    return float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (sx * sy))


def _distribution(values: np.ndarray) -> dict:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"values": values, "p10": float("nan"), "p50": float("nan"), "p90": float("nan"),
                "mean": float("nan"), "p_negative": float("nan")}
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    return {"values": values, "p10": float(p10), "p50": float(p50), "p90": float(p90), "mean": float(values.mean()),
            "p_negative": float((values < 0).mean())}


def summarise(plan: dict, x: np.ndarray, parts: list, track: dict, top_inputs: int = 15, top_packages: int = 10) -> dict:
    """Combine slices from `run_draws` into the Monte Carlo report."""
    best = np.concatenate([p["best"] for p in parts])
    best_value = np.concatenate([p["best_value"] for p in parts])
    tracked = {name: np.concatenate([p["tracked"][name] for p in parts]) for name in track}
    n = best.size
    solved = best >= 0
    masks = optimizer.MASKS[best[solved]]
    inclusion = {oid: float(masks[:, i].mean()) if solved.any() else 0.0 for i, oid in enumerate(OPTION_IDS)}
    codes, counts = np.unique(best[solved], return_counts=True)
    order = np.lexsort((codes, SIZES[codes], -counts))[:top_packages]
    packages = [{"code": int(codes[i]), "ids": optimizer.ids_of(int(codes[i])), "count": int(counts[i]),
                 "share": counts[i] / n} for i in order]
    correlations = []
    for column, iid in enumerate(plan["ids"]):
        rho = spearman(x[:n, column][solved], best_value[solved]) if solved.sum() > 2 else 0.0
        correlations.append({"id": iid, "rho": rho})
    correlations.sort(key=lambda r: -abs(r["rho"]))
    return {
        "n": n,
        "sampled_inputs": len(plan["ids"]),
        "inclusion": inclusion,
        "packages": packages,
        "distinct_packages": int(codes.size),
        "hold_share": {name: float((best == code).mean()) for name, code in track.items()},
        "optimal": _distribution(best_value),
        "tracked": {name: _distribution(values) for name, values in tracked.items()},
        "beats": {name: float((values >= best_value - optimizer.TIE).mean()) for name, values in tracked.items()},
        "spearman": correlations[:top_inputs],
    }


def monte_carlo(state: InputState, specs, settings: Settings = Settings(), n: int = 1000, seed: int = 42,
                track: dict | None = None, curves: dict = JRC_CURVES, chunk: int = 50, progress=None) -> dict:
    """Run the whole Monte Carlo in one call. `progress(fraction)` is called after each chunk."""
    track = track or {}
    plan = sampling_plan(state, specs)
    x = draw(plan, n, seed)
    parts = []
    for start in range(0, n, chunk):
        parts.append(run_draws(state, plan, x, start, min(n, start + chunk), settings, track, curves))
        if progress:
            progress(min(n, start + chunk) / n)
    return summarise(plan, x, parts, track)
