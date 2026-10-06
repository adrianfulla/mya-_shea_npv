"""Package evaluation and optimisation. No Streamlit.

The engine's formulas are not repeated here for the options themselves: `precompute()` runs
the existing engine once per input set and keeps each option's year arrays (capex, other
benefit, opex, supplier income) and the supply-effect parameters. A package is then cheap
array math that follows the Portfolio sheet row by row:

  * supply effects combine as 1 - prod(1 - r), with the loyalty effect counted once;
  * three interactions: flood works lower insurance recoveries, the battery is redundant
    when the genset is on, PPA and owned PV are alternatives;
  * tax is charged on the combined operating flow;
  * free cash flow and supplier income are cut at the package horizon G02a.

`tests/test_optimizer.py` checks all 8,192 packages against `engine.run_model`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import engine
from .engine import JRC_CURVES, N_YEARS, OPTION_IDS, OPTION_NAMES, YEARS

OBJECTIVES = ("factory NPV", "factory + supplier NPV")
N_OPTIONS = len(OPTION_IDS)
N_PACKAGES = 1 << N_OPTIONS  # 8,192
TIE = 1e-6  # USD: packages closer than this are treated as equal, and the leaner one wins

_IX = {oid: i for i, oid in enumerate(OPTION_IDS)}
_POS = YEARS > 0

# Every package as a row of 0/1 flags: MASKS[code, i] = 1 if option i is in package `code`.
_CODES = np.arange(N_PACKAGES)
MASKS = ((_CODES[:, None] >> np.arange(N_OPTIONS)) & 1).astype(float)
SIZES = MASKS.sum(axis=1)

# The combined supply block reads only these six switches, so there are 64 distinct cases.
_SUPPLY_OPTIONS = ("1", "2", "3", "5", "8", "9")
_COMBO = sum(MASKS[:, _IX[oid]].astype(int) << bit for bit, oid in enumerate(_SUPPLY_OPTIONS))
_COMBO_FLAGS = ((np.arange(64)[:, None] >> np.arange(6)) & 1).astype(float)

_PAIR_FLOOD = MASKS[:, _IX["6"]] * MASKS[:, _IX["10"]]
_PAIR_BATTERY = MASKS[:, _IX["7b"]] * MASKS[:, _IX["7d"]]
_PAIR_PPA = MASKS[:, _IX["7a"]] * MASKS[:, _IX["7c"]]


def code_of(include) -> int:
    """Package code for include switches (dict option id -> bool) or an iterable of option ids."""
    if isinstance(include, dict):
        return sum(1 << _IX[oid] for oid, on in include.items() if on)
    return sum(1 << _IX[oid] for oid in include)


def ids_of(code: int) -> tuple:
    return tuple(oid for i, oid in enumerate(OPTION_IDS) if code >> i & 1)


def include_of(code: int) -> dict:
    return {oid: bool(code >> i & 1) for i, oid in enumerate(OPTION_IDS)}


# --- precompute: one engine run per input set ----------------------------------------------

@dataclass(frozen=True)
class Precomputed:
    """Everything a package evaluation needs, for one set of input values."""

    df: np.ndarray  # discount factor by year
    tax_rate: np.ndarray
    active: np.ndarray  # package horizon flag, year <= G02a
    capex: np.ndarray  # [option, year]
    other: np.ndarray
    opex: np.ndarray
    supplier: np.ndarray  # already cut at each option's own horizon, as in the Engine sheet
    own_supply: np.ndarray  # each option's standalone supply benefit (for reference only)
    flood_insurance: np.ndarray  # (1 - N05) x H05 x (c_rec0 - c_rec1), zero in year 0
    supply_benefit: np.ndarray  # [64 supply cases, year]: combined supply benefit
    standalone_npv: np.ndarray  # each option's own NPV, from the Engine sheet


def precompute(values: dict, curves: dict = JRC_CURVES) -> Precomputed:
    """Run the engine once and keep the per-option year arrays and the combined supply cases."""
    v = values
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        tl = engine.timeline(v)
        vc = engine.value_chain(v)
        c = engine.calc(v, vc, curves)
        sup = engine.supply(v, c)
        opts = engine.option_cashflows(v, c, sup, tl)

        def rows(name):
            return np.array([opts[oid][name] for oid in OPTION_IDS])

        # Supply sheet, portfolio block (rows 52-57), for all 64 combinations of the six switches.
        b1, b2, b3, b5, b8, b9 = _COMBO_FLAGS.T
        blocks = sup["options"]
        decline = v["H04"] - b5 * v["S04"] - b9 * v["F05"] * v["F03"]
        reduction = 1 - (
            (1 - v["I05"] * np.maximum(b1 * v["P01"], b5 * c["c_stove_served"]))
            * (1 - b2 * blocks["2"]["reduction"])
            * (1 - b8 * blocks["8"]["reduction"])
            * (1 - b9 * blocks["9"]["reduction"])
        )
        survival = np.minimum(1, v["R03"] + b1 * v["I06"])
        regeneration = (
            blocks["3"]["new"][None, :] * survival[:, None] / v["R03"] if v["R03"] != 0 else np.zeros((64, N_YEARS))
        )
        new = b2[:, None] * blocks["2"]["new"][None, :] + b3[:, None] * regeneration
        c01 = v["C01"]
        q = np.where(_POS, c01 * v["C06"] * (1 - decline)[:, None] ** YEARS + new, 0.0)
        loss = np.where(
            _POS,
            (np.maximum(0, c01 - q) + v["H01"] * v["H02"] * np.minimum(c01, q) * (1 - reduction)[:, None]) * c["c_VST"],
            0.0,
        )
        supply_benefit = sup["baseline"]["loss"][None, :] - loss

        flood_insurance = (1 - v["N05"]) * np.where(_POS, v["H05"] * (c["c_rec0"] - c["c_rec1"]), 0.0)

    return Precomputed(
        df=tl["df"],
        tax_rate=tl["tax_rate"],
        active=(YEARS <= v["G02a"]).astype(float),
        capex=rows("capex"),
        other=rows("other"),
        opex=rows("opex"),
        supplier=rows("supplier"),
        own_supply=rows("supply"),
        flood_insurance=flood_insurance,
        supply_benefit=supply_benefit,
        standalone_npv=np.array([opts[oid]["npv"] for oid in OPTION_IDS]),
    )


def _as_precomputed(inputs, curves=JRC_CURVES) -> Precomputed:
    return inputs if isinstance(inputs, Precomputed) else precompute(inputs, curves)


# --- one package: the Portfolio sheet as array math ----------------------------------------

def evaluate_package(inputs, include, curves: dict = JRC_CURVES) -> dict:
    """Evaluate one package exactly as the Portfolio sheet does.

    `inputs` is a dict of active input values, or a `Precomputed` to reuse. `include` is a dict
    option id -> bool (or an iterable of option ids). Returns the factory NPV, the NPV including
    supplier income, the PV of capex, the upfront capex and the annual cash-flow table.
    """
    pre = _as_precomputed(inputs, curves)
    code = code_of(include)
    m = MASKS[code]
    capex = m @ pre.capex
    supply = pre.supply_benefit[_COMBO[code]]
    other = m @ pre.other
    int_flood_ins = -_PAIR_FLOOD[code] * pre.flood_insurance
    int_bat_gen = -_PAIR_BATTERY[code] * pre.other[_IX["7b"]]
    int_ppa_pv = -_PAIR_PPA[code] * pre.other[_IX["7c"]]
    opex = m @ pre.opex
    opflow = supply + other + int_flood_ins + int_bat_gen + int_ppa_pv - opex
    tax = pre.tax_rate * np.maximum(0, opflow)
    fcf = (opflow - capex - tax) * pre.active
    supplier = (m @ pre.supplier) * pre.active
    npv = float(fcf @ pre.df)
    supplier_pv = float(supplier @ pre.df)
    return {
        "code": code,
        "ids": ids_of(code),
        "include": include_of(code),
        "npv": npv,  # Portfolio!F5
        "supplier_pv": supplier_pv,
        "npv_with_suppliers": npv + supplier_pv,  # Portfolio!F7
        "capex_pv": float(capex @ pre.df),  # Portfolio!F6
        "capex_year0": float(capex[0]),  # upfront outlay: the first year of the timeline
        "capex_year1": float(capex[1]),
        "cashflows": {
            "year": YEARS.astype(int),
            "active": pre.active,
            "capex": capex,
            "supply": supply,
            "other": other,
            "int_flood_ins": int_flood_ins,
            "int_bat_gen": int_bat_gen,
            "int_ppa_pv": int_ppa_pv,
            "opex": opex,
            "opflow": opflow,
            "tax": tax,
            "fcf": fcf,
            "supplier": supplier,
            "df": pre.df,
        },
    }


# --- all 8,192 packages at once ------------------------------------------------------------

@dataclass(frozen=True)
class Enumeration:
    """Headline numbers of every package, indexed by package code."""

    npv: np.ndarray
    supplier_pv: np.ndarray
    capex_pv: np.ndarray
    capex_year0: np.ndarray

    def objective(self, objective: str) -> np.ndarray:
        if objective == OBJECTIVES[0]:
            return self.npv
        if objective == OBJECTIVES[1]:
            return self.npv + self.supplier_pv
        raise ValueError(f"objective must be one of {OBJECTIVES}, got {objective!r}")

    def package(self, code: int, objective: str = OBJECTIVES[0]) -> dict:
        code = int(code)
        return {
            "code": code,
            "ids": ids_of(code),
            "include": include_of(code),
            "npv": float(self.npv[code]),
            "supplier_pv": float(self.supplier_pv[code]),
            "npv_with_suppliers": float(self.npv[code] + self.supplier_pv[code]),
            "capex_pv": float(self.capex_pv[code]),
            "capex_year0": float(self.capex_year0[code]),
            "objective": float(self.objective(objective)[code]),
        }


def enumerate_packages(inputs, curves: dict = JRC_CURVES) -> Enumeration:
    """NPV, supplier income PV and capex PV of all 8,192 packages, vectorised.

    Everything except tax is linear in the include flags, so it collapses to one number per
    option, per supply case and per interaction. Only the taxed years need year-by-year work.
    """
    pre = _as_precomputed(inputs, curves)
    weight = pre.active * pre.df  # free cash flow counts only inside the package horizon
    battery, ppa = pre.other[_IX["7b"]], pre.other[_IX["7c"]]

    pre_tax = (
        MASKS @ ((pre.other - pre.opex - pre.capex) @ weight)
        + (pre.supply_benefit @ weight)[_COMBO]
        - _PAIR_FLOOD * (pre.flood_insurance @ weight)
        - _PAIR_BATTERY * (battery @ weight)
        - _PAIR_PPA * (ppa @ weight)
    )
    taxed = np.flatnonzero(pre.tax_rate * weight)
    if taxed.size:
        opflow = (
            pre.supply_benefit[:, taxed][_COMBO]
            + MASKS @ (pre.other - pre.opex)[:, taxed]
            - np.outer(_PAIR_FLOOD, pre.flood_insurance[taxed])
            - np.outer(_PAIR_BATTERY, battery[taxed])
            - np.outer(_PAIR_PPA, ppa[taxed])
        )
        tax_pv = np.maximum(0, opflow) @ (pre.tax_rate * weight)[taxed]
    else:
        tax_pv = 0.0
    return Enumeration(
        npv=pre_tax - tax_pv,
        supplier_pv=MASKS @ (pre.supplier @ weight),
        capex_pv=MASKS @ (pre.capex @ pre.df),
        capex_year0=MASKS @ pre.capex[:, 0],
    )


def _as_enumeration(inputs, curves=JRC_CURVES) -> Enumeration:
    return inputs if isinstance(inputs, Enumeration) else enumerate_packages(inputs, curves)


# --- optimise ------------------------------------------------------------------------------

def feasible(enum: Enumeration, budget=None, forced_in=(), forced_out=()) -> np.ndarray:
    """Boolean mask over package codes: forced options respected, capex PV within the budget."""
    clash = set(forced_in) & set(forced_out)
    if clash:
        raise ValueError(f"options forced both in and out: {sorted(clash)}")
    ok = np.ones(N_PACKAGES, dtype=bool)
    for oid in forced_in:
        ok &= MASKS[:, _IX[oid]] == 1
    for oid in forced_out:
        ok &= MASKS[:, _IX[oid]] == 0
    if budget is not None:
        ok &= enum.capex_pv <= budget + TIE
    return ok


def best_code(score: np.ndarray, ok: np.ndarray, capex_pv: np.ndarray):
    """Code of the best feasible package, or None. Ties go to the fewest options, then the least capex."""
    if not ok.any():
        return None
    top = score[ok].max()
    tied = np.flatnonzero(ok & (score >= top - TIE))
    if tied.size == 1:
        return int(tied[0])
    return int(tied[np.lexsort((tied, capex_pv[tied], SIZES[tied]))[0]])


def _ranking(score, ok, capex_pv, n):
    """The n best feasible packages, best first. A package that only adds options with no effect
    on the score to a better-or-equal one (for example the PPA on top of owned PV) is left out."""
    first = best_code(score, ok, capex_pv)
    if first is None:
        return []
    kept = [first]
    codes = np.flatnonzero(ok)
    for code in codes[np.lexsort((codes, capex_pv[codes], SIZES[codes], -score[codes]))]:
        code = int(code)
        if len(kept) >= n and score[code] < score[kept[-1]] - TIE:
            break
        duplicate = code == first
        for position, other in enumerate(kept):
            if duplicate or abs(score[other] - score[code]) > TIE:
                continue
            if other & code == other:  # a leaner equivalent is already listed
                duplicate = True
            elif other & code == code and position > 0:  # this one is the leaner equivalent
                kept[position] = code
                duplicate = True
        if not duplicate:
            kept.append(code)
    return kept[:n]


def optimize(inputs, objective: str = OBJECTIVES[0], budget=None, forced_in=(), forced_out=(), top: int = 10,
             curves: dict = JRC_CURVES) -> dict:
    """Search all 8,192 packages for the best one.

    `inputs`: dict of active input values, a `Precomputed`, or an `Enumeration` to reuse.
    `objective`: "factory NPV" or "factory + supplier NPV". `budget` caps the PV of capex.
    Returns the best package, and `top`: the best packages in order, the optimum first.
    """
    enum = _as_enumeration(inputs, curves)
    score = enum.objective(objective)
    ok = feasible(enum, budget, forced_in, forced_out)
    ranking = [enum.package(code, objective) for code in _ranking(score, ok, enum.capex_pv, top)]
    return {
        "objective": objective,
        "budget": budget,
        "forced_in": tuple(forced_in),
        "forced_out": tuple(forced_out),
        "feasible": bool(ranking),
        "n_feasible": int(ok.sum()),
        "best": ranking[0] if ranking else None,
        "top": ranking,
    }


def best_package(enum: Enumeration, objective: str = OBJECTIVES[0], budget=None, forced_in=(), forced_out=()):
    """Just the optimum (a package dict, or None when nothing is feasible). For scans."""
    code = best_code(enum.objective(objective), feasible(enum, budget, forced_in, forced_out), enum.capex_pv)
    return None if code is None else enum.package(code, objective)


def frontier(inputs, objective: str = OBJECTIVES[0], levels: int = 25, forced_in=(), forced_out=(),
             curves: dict = JRC_CURVES) -> list:
    """Best package at `levels` capex budgets, from $0 to the capex PV of the unconstrained optimum.

    Each row: budget, the best affordable package (None if nothing is feasible), and the options
    that entered and left compared with the previous budget level.
    """
    enum = _as_enumeration(inputs, curves)
    score = enum.objective(objective)
    forced = feasible(enum, None, forced_in, forced_out)
    unconstrained = best_code(score, forced, enum.capex_pv)
    if unconstrained is None:
        return []
    ceiling = float(enum.capex_pv[unconstrained])
    budgets = np.linspace(0.0, ceiling, levels) if ceiling > 0 else np.array([0.0])
    rows, previous = [], ()
    for budget in budgets:
        code = best_code(score, forced & (enum.capex_pv <= budget + TIE), enum.capex_pv)
        package = None if code is None else enum.package(code, objective)
        ids = package["ids"] if package else ()
        rows.append({
            "budget": float(budget),
            "package": package,
            "entered": tuple(oid for oid in ids if oid not in previous),
            "left": tuple(oid for oid in previous if oid not in ids),
        })
        previous = ids
    return rows


def names(ids, short: dict | None = None) -> str:
    """Readable package label."""
    labels = short or OPTION_NAMES
    return " + ".join(labels[oid] for oid in ids) if ids else "No options"
