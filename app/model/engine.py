"""Calculation engine: a one-for-one translation of the workbook's formulas.

Pure functions, no Streamlit. Every function takes `v`, a dict {input ID: active value}
(the workbook's `in_<ID>` names), and returns plain dicts of floats and numpy arrays.
MODEL_SPEC.md maps each formula here to its workbook cell.
"""
from __future__ import annotations

import math
from bisect import bisect_right

import numpy as np

N_YEARS = 31
YEARS = np.arange(N_YEARS, dtype=float)  # columns D..AH, years 0..30

OPTION_IDS = ("1", "2", "3", "4", "5", "6", "7a", "7b", "7c", "7d", "8", "9", "10")
OPTION_NAMES = {
    "1": "1 Solar irrigation (nurseries + gardens)",
    "2": "2 Apiaries",
    "3": "3 Regeneration & agroforestry",
    "4": "4 Biochar at village processing centres",
    "5": "5 Processing stoves for butter suppliers",
    "6": "6 Flood drainage, detention, plinths",
    "7a": "7a Solar PV, grid-tied",
    "7b": "7b Battery + islanding (critical loads)",
    "7c": "7c Solar PPA (no capex)",
    "7d": "7d Diesel genset (comparator)",
    "8": "8 Mango toll pilot (substitution hedge)",
    "9": "9 Community fire management",
    "10": "10 Insurance: property incl. flood + BI",
}
SUPPLY_OPTIONS = ("1", "2", "3", "5", "8", "9")

# Hazards!A5:C10, JRC depth-damage curves for industrial buildings.
JRC_CURVES = {
    "depth": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
    "africa": [0.0, 0.0627, 0.2472, 0.4033, 0.4945, 0.6847],
    "global": [0.0, 0.297, 0.48, 0.603, 0.694, 0.82],
}

_POS = YEARS > 0
_ZERO = np.zeros(N_YEARS)


# --- helpers -------------------------------------------------------------------------------

def _div(num, den, warn=None, what=""):
    """num / den. Excel returns #DIV/0! for a zero denominator; here the term is 0 and noted."""
    if den == 0:
        if warn is not None:
            warn.append(f"Division by zero in {what}: Excel would show #DIV/0!; the term is set to 0.")
        return np.zeros_like(num, dtype=float) if isinstance(num, np.ndarray) else 0.0
    return num / den


def _mod0(t, n, warn=None, what=""):
    """Excel's MOD(t, n) = 0, elementwise (MOD(t, n) = t - n*INT(t/n))."""
    if n == 0:
        if warn is not None:
            warn.append(f"MOD by zero in {what}: Excel would show #DIV/0!; no replacement is scheduled.")
        return np.zeros(t.shape, dtype=bool)
    return (t - n * np.floor(t / n)) == 0


def _y0(x):
    """=IF(year=0, 0, x)"""
    return np.where(_POS, x, 0.0)


def damage_share(depth: float, h09: float, curves: dict = JRC_CURVES) -> float:
    """Calc!C19/C20: damage share at a flood depth, linear interpolation on the Hazards curve."""
    depths = curves["depth"]
    col = curves["africa"] if int(h09) == 1 else curves["global"]  # CHOOSE(in_H09, Africa, Global)
    x = min(max(depth, 0.0), 3.0)
    i = bisect_right(depths, x) - 1  # MATCH(x, depths, 1): last point <= x
    if i >= len(depths) - 1:
        return col[-1]
    return col[i] + (col[i + 1] - col[i]) * (x - depths[i]) / (depths[i + 1] - depths[i])


def timeline(v: dict) -> dict:
    """Engine rows 4-5: tax rate and discount factor by year."""
    tax_rate = np.where(YEARS <= v["G04a"], 0.0, v["G05"] * v["G04b"] + (1 - v["G05"]) * v["G04c"])
    df = 1.0 / (1.0 + v["G01"]) ** YEARS
    return {"years": YEARS, "tax_rate": tax_rate, "df": df}


# --- Value chain sheet ---------------------------------------------------------------------

def _channel(lines, price, amazon_rows=()):
    """lines: (key, label, usd, ghana share, inputs used). Adds % of price, stays and leaves."""
    out = []
    for key, label, usd, share, used in lines:
        stays = usd * share
        out.append({"key": key, "label": label, "usd": usd, "pct": _div(usd, price), "gh_share": share,
                    "stays": stays, "leaves": usd - stays, "inputs": used})
    total = sum(l["usd"] for l in out)
    stays = sum(l["stays"] for l in out)
    channel = {"lines": out, "price": total, "pct": sum(l["pct"] for l in out), "stays": stays,
               "leaves": sum(l["leaves"] for l in out), "share_gh": _div(stays, total)}
    if amazon_rows:
        channel["share_amazon"] = _div(sum(l["usd"] for l in out if l["key"] in amazon_rows), total)
    return channel


def value_chain(v: dict, warn: list | None = None) -> dict:
    """Value chain sheet: per-jar cost lines for both channels, summary and whole-business totals."""
    g05 = v["G05"]
    crude_kg = _div(v["V02"] / 1000 * v["V03"], 1 - v["V04"], warn, "crude butter per jar (V04)")  # B4
    units = _div(v["C01"] * 1000 * v["C12"], crude_kg, warn, "retail units per year (V02, V03)")  # B5
    units_amz = units * g05  # B6
    crude = crude_kg * v["C02"]

    shared = [
        ("crude", "Crude shea butter (paid to women processors)", crude, 1.0, "C02, V02–V04"),
        ("ingredients", "Other ingredients", v["V05"], 1 - v["V06"], "V05, V06"),
        ("packaging", "Packaging", v["V07"], 1 - v["V08"], "V07, V08"),
        ("conversion", "Factory conversion in Tema", v["V09"], 1.0, "V09"),
    ]
    port = ("port", "Ghana port, handling, documents", v["V10"], 1.0, "V10")
    fob = sum(l[2] for l in shared) + port[2]  # B7 = SUM(B11:B15)
    referral = (v["V32"] if v["V01"] <= 10 else v["V24"]) * v["V01"]
    amazon_costs = shared + [
        port,
        ("freight", "Ocean freight to US", _div(v["V12"], v["V11"], warn, "freight per jar (V11)"), 0.0, "V11, V12"),
        ("duty", "US import duty and tariff", (v["V13"] + v["V14"]) * fob, 0.0, "V13, V14"),
        ("broker", "US broker, drayage, trucking", v["V15"], 0.0, "V15"),
        ("awd_storage", "Amazon AWD storage", v["V16"] * v["V18"] * v["V17"], 0.0, "V16–V18"),
        ("awd_processing", "Amazon AWD processing", _div(v["V20"], v["V19"], warn, "AWD processing (V19)"), 0.0, "V19, V20"),
        ("awd_transport", "Amazon AWD transport to FBA", v["V21"] * v["V18"], 0.0, "V18, V21"),
        ("fba_fee", "Amazon FBA fulfillment fee", v["V22"], 0.0, "V22"),
        ("fba_storage", "Amazon FBA storage (1 month)", v["V23"] * v["V18"], 0.0, "V18, V23"),
        ("referral", "Amazon referral fee", referral, 0.0, "V01, V24, V32"),
        ("advertising", "Amazon advertising", v["V25"] * v["V01"], 0.0, "V25"),
        ("returns", "Returns and damages", v["V26"] * v["V01"], 0.0, "V26"),
        ("plan", "Amazon Professional plan", v["V31"] * 12 / units_amz if units_amz > 0 else 0.0, 0.0, "V31"),
    ]
    amazon_margin = v["V01"] - sum(l[2] for l in amazon_costs)  # B28
    amazon = _channel(
        amazon_costs + [("margin", "Brand contribution margin (Ghanaian company)", amazon_margin, 1.0, "")],
        v["V01"],
        amazon_rows=("awd_storage", "awd_processing", "awd_transport", "fba_fee", "fba_storage", "referral",
                     "advertising", "plan"),  # E31 = (SUM(B19:B25) + B27) / B29
    )

    domestic_costs = shared + [
        ("distribution", "Domestic distribution", v["V30"], 1.0, "V30"),
        ("retailer", "Retailer and distributor margin", v["V28"] * v["V27"], 1.0, "V28"),
        ("vat", "VAT and levies", v["V27"] * v["V29"] / (1 + v["V29"]), 1.0, "V29"),
    ]
    domestic_margin = v["V27"] - sum(l[2] for l in domestic_costs)  # B41
    domestic = _channel(
        domestic_costs + [("margin", "Brand contribution margin (Ghanaian company)", domestic_margin, 1.0, "")],
        v["V27"],
    )

    def weighted(a, b):
        return g05 * a + (1 - g05) * b

    price = weighted(amazon["price"], domestic["price"])  # D47
    stays = weighted(amazon["stays"], domestic["stays"])  # D48
    women = weighted(crude, crude)  # D50
    brand = weighted(amazon_margin, domestic_margin)  # D52 = c_unit_margin
    summary = {
        "price": {"amazon": amazon["price"], "domestic": domestic["price"], "weighted": price},
        "stays": {"amazon": amazon["stays"], "domestic": domestic["stays"], "weighted": stays},
        "share_gh": {"amazon": amazon["share_gh"], "domestic": domestic["share_gh"], "weighted": _div(stays, price)},
        "women": {"amazon": crude, "domestic": crude, "weighted": women},
        "women_share": {"amazon": _div(crude, amazon["price"]), "domestic": _div(crude, domestic["price"]),
                        "weighted": _div(women, price)},
        "brand_margin": {"amazon": amazon_margin, "domestic": domestic_margin, "weighted": brand},
    }

    retail_revenue = units * price  # B56
    retail_stays = units * stays  # B57
    bulk_revenue = v["C01"] * (1 - v["C12"]) * (1 - v["V04"]) * 1000 * v["C15"]  # B58
    total_revenue = retail_revenue + bulk_revenue  # B59 = c_sales
    revenue_stays = retail_stays + bulk_revenue  # B60
    business = {
        "retail_revenue": retail_revenue,
        "retail_stays": retail_stays,
        "bulk_revenue": bulk_revenue,
        "total_revenue": total_revenue,
        "revenue_stays": revenue_stays,
        "share_stays": _div(revenue_stays, total_revenue, warn, "share of total revenue staying in Ghana"),
    }
    return {
        "crude_kg": crude_kg,
        "units": units,
        "units_amz": units_amz,
        "fob": fob,
        "amazon": amazon,
        "domestic": domestic,
        "summary": summary,
        "business": business,
        "margin_vc": _div(brand, crude_kg) * 1000,  # B62 = c_margin_vc
    }


def export_share_curve(vc: dict, share):
    """Value chain B66:B71: share of jar price staying in Ghana at export share(s) `share`."""
    x = np.asarray(share, dtype=float)
    a, d = vc["amazon"], vc["domestic"]
    return (x * a["stays"] + (1 - x) * d["stays"]) / (x * a["price"] + (1 - x) * d["price"])


# --- Calc sheet ----------------------------------------------------------------------------

def calc(v: dict, vc: dict, curves: dict = JRC_CURVES, warn: list | None = None) -> dict:
    """Calc sheet: derived quantities, keyed by the workbook's defined names."""
    c = {}
    c["c_margin"] = v["C03"] if v["C11"] == 1 else v["C12"] * vc["margin_vc"] + (1 - v["C12"]) * v["C03"]
    c["c_VST"] = (1 - v["C05"]) * v["C04"] + v["C05"] * c["c_margin"]
    c["c_kernel"] = _div(v["C01"], v["C13"], warn, "kernel needed (C13)")
    c["c_catch"] = _div(c["c_kernel"] * 1000, v["R05"] * v["F06"], warn, "sourcing area (R05, F06)")
    c["c_annual"] = v["C01"] * c["c_margin"]
    c["c_daily"] = _div(c["c_annual"], v["C14"], warn, "daily margin (C14)")
    c["c_hourly"] = _div(c["c_daily"], v["C09"], warn, "hourly margin (C09)")
    c["c_out_hrs"] = v["H08"] * v["C09"] / 24 * v["C14"] / 365
    c["c_avg_kw"] = _div(v["C08"] * 1000, v["C14"] * v["C09"], warn, "average plant load (C14, C09)")
    c["c_out_prod"] = c["c_out_hrs"] * c["c_hourly"] * v["C10"]
    c["c_out_spoil"] = _div(c["c_out_hrs"], v["H10"], warn, "outage events (H10)") * v["E07"]
    c["c_out_cost"] = c["c_out_prod"] + c["c_out_spoil"]
    c["c_bat_avoid"] = v["E14"] * (
        c["c_out_spoil"] + c["c_out_prod"] * min(1, _div(v["E06a"], c["c_avg_kw"], warn, "battery load cover (C08)"))
    )
    c["c_gen_avoid"] = c["c_out_spoil"] + c["c_out_prod"] * min(
        1, _div(v["E25"], c["c_avg_kw"], warn, "genset load cover (C08)")
    )
    sales = vc["business"]["total_revenue"]
    c["c_out_pct"] = c["c_out_cost"] / sales if sales > 0 else 0.0
    c["c_D0"] = damage_share(v["H06"], v["H09"], curves)
    c["c_D1"] = damage_share(v["D07"], v["H09"], curves)
    c["c_prop0"] = v["C07"] * c["c_D0"]
    c["c_prop1"] = v["C07"] * c["c_D1"]
    c["c_bi0"] = v["H07"] * c["c_daily"] * v["C10"]
    c["c_bi1"] = v["D09"] * c["c_daily"] * v["C10"]
    c["c_floss0"] = c["c_prop0"] + c["c_bi0"]
    c["c_floss1"] = c["c_prop1"] + c["c_bi1"]
    c["c_rec0"] = v["N04"] * (max(0, c["c_prop0"] - max(v["N03a"] * c["c_prop0"], v["N03b"])) + c["c_bi0"])
    c["c_rec1"] = v["N04"] * (max(0, c["c_prop1"] - max(v["N03a"] * c["c_prop1"], v["N03b"])) + c["c_bi1"])
    c["c_premium"] = v["N01"] * v["C07"] + v["N02"] * c["c_annual"]
    c["c_flood_cap"] = v["D02"] * v["D01"] * (1 + v["D03"]) + v["D05"] * v["D04"] + v["D06"]
    c["c_pv_cap"] = v["E09"] * v["E01"]
    c["c_bat_kwh"] = _div(v["E06a"] * v["E06b"], v["E16"] * v["E17"] * v["E18"], warn, "battery nameplate (E16–E18)")
    c["c_bat_cap"] = c["c_bat_kwh"] * v["E05"] + v["E13"]
    c["c_gen_kva"] = v["E25"] * v["E19"]
    c["c_gen_cap"] = c["c_gen_kva"] * v["E10"]
    c["c_gen_fuel"] = c["c_out_hrs"] * min(v["E25"], c["c_avg_kw"]) * v["E04"]
    c["c_irr_cap"] = v["I03"] * (v["I01"] + (_div(1, v["I02"], warn, "borehole success rate (I02)") - 1) * v["I08"])
    c["c_api_share"] = min(
        1,
        _div(_div(v["A08"], v["A10"], warn, "apiaries (A10)") * math.pi * v["A11"] ** 2 * 100, c["c_catch"], warn,
             "apiary share of sourcing area"),
    )
    c["c_wax"] = v["A08"] * v["A04"] * v["A02"] * v["A03"]
    c["c_regen_ha"] = _div(v["R07"], v["R09"], warn, "regeneration area (R09)")
    c["c_stove_served"] = min(1, _div(v["S05"] * v["S06"], v["C01"], warn, "stove-served share (C01)"))
    c["c_char"] = v["B01"] * v["B02"]
    c["c_mango_yield"] = v["M01a"] if v["M10"] == 1 else v["M01b"]
    c["c_mango_t"] = v["M06"] * c["c_mango_yield"] / 1000
    c["c_mango_margin"] = v["M06"] * c["c_mango_yield"] * (v["M02"] - v["M04"]) - v["M06"] * v["M03"]
    c["c_mango_mpt"] = c["c_mango_margin"] / c["c_mango_t"] if c["c_mango_t"] > 0 else 0.0
    c["c_sub_t"] = min(c["c_mango_t"], v["M05"] * v["H02"] * v["C01"]) * (1 - v["G07"])
    return c


# --- Supply sheet --------------------------------------------------------------------------

def supply(v: dict, c: dict, include: dict | None = None, warn: list | None = None) -> dict:
    """Supply sheet: butter supply and the value of supply lost, baseline and per option.

    `include` (option id -> 0/1) drives the combined portfolio block.
    """
    include = include or {}
    inc = {oid: float(bool(include.get(oid, False))) for oid in OPTION_IDS}
    t = YEARS
    c01, vst = v["C01"], c["c_VST"]
    poor = v["H01"] * v["H02"]
    natural = v["C01"] * v["C06"]

    def block(d, reduction, new):
        q = _y0(natural * (1 - d) ** t + new)
        loss = _y0((np.maximum(0, c01 - q) + poor * np.minimum(c01, q) * (1 - reduction)) * vst)
        return {"d": d, "reduction": reduction, "new": _y0(new), "q": q, "loss": loss}

    baseline = block(v["H04"], 0.0, _ZERO)
    base_q, base_loss = baseline["q"], baseline["loss"]

    ramp = np.minimum(1, np.maximum(0, _div(t - v["R04a"], v["R04b"] - v["R04a"], warn, "regeneration ramp (R04a = R04b)")))
    new2 = v["A06"] * c["c_api_share"] * base_q
    new3 = v["R07"] * v["R03"] * v["R05"] / 1000 * v["C13"] * ramp * v["R06"]

    options = {
        "1": block(v["H04"], v["P01"] * v["I05"], _ZERO),
        "2": block(v["H04"], c["c_api_share"] * v["A07"] * v["H03"], new2),
        "3": block(v["H04"], 0.0, new3),
        "5": block(v["H04"] - v["S04"], c["c_stove_served"] * v["I05"], _ZERO),
        "8": block(v["H04"], _div(c["c_sub_t"], v["H02"] * v["C01"], warn, "mango substitution share (H02, C01)"), _ZERO),
        "9": block(v["H04"] - v["F05"] * v["F03"], v["F03"] * v["H03"] * v["F04"], _ZERO),
    }
    for blk in options.values():
        blk["benefit"] = base_loss - blk["loss"]

    d_p = v["H04"] - inc["5"] * v["S04"] - inc["9"] * v["F05"] * v["F03"]
    red_p = 1 - (
        (1 - v["I05"] * max(inc["1"] * v["P01"], inc["5"] * c["c_stove_served"]))
        * (1 - inc["2"] * options["2"]["reduction"])
        * (1 - inc["8"] * options["8"]["reduction"])
        * (1 - inc["9"] * options["9"]["reduction"])
    )
    new_p = inc["2"] * options["2"]["new"] + inc["3"] * _div(
        options["3"]["new"] * min(1, v["R03"] + inc["1"] * v["I06"]), v["R03"], warn, "regeneration survival (R03)"
    )
    portfolio_block = block(d_p, red_p, new_p)
    portfolio_block["benefit"] = base_loss - portfolio_block["loss"]

    return {"baseline": baseline, "options": options, "portfolio": portfolio_block}


# --- Engine sheet --------------------------------------------------------------------------

def _block(oid, horizon, capex, supply_benefit, other, opex, supplier, tl):
    """One Engine block: Active, Capex, benefits, Opex, Operating flow, Tax, FCF, Supplier income."""
    df = tl["df"]
    active = (YEARS <= horizon).astype(float)
    opflow = supply_benefit + other - opex
    tax = tl["tax_rate"] * np.maximum(0, opflow)
    fcf = (opflow - capex - tax) * active
    supplier = supplier * active
    supply_active = supply_benefit * active
    return {
        "id": oid,
        "name": OPTION_NAMES[oid],
        "horizon": horizon,
        "active": active,
        "capex": capex,
        "supply": supply_benefit,
        "other": other,
        "opex": opex,
        "opflow": opflow,
        "tax": tax,
        "fcf": fcf,
        "supplier": supplier,
        "supply_active": supply_active,
        "npv": float(fcf @ df),
        "capex_pv": float(capex @ df),
        "supplier_pv": float(supplier @ df),
        "supply_pv": float(supply_active @ df),
    }


def option_cashflows(v: dict, c: dict, sup: dict, tl: dict | None = None, warn: list | None = None) -> dict:
    """Engine sheet: one cash-flow block per option, keyed by option id."""
    tl = tl or timeline(v)
    t = YEARS
    g02a, g02b = v["G02a"], v["G02b"]
    grant = 1 - v["P02"]
    benefit = {oid: sup["options"][oid]["benefit"] for oid in SUPPLY_OPTIONS}
    pv_kwh = np.minimum(v["C08"] * 1000, v["E09"] * v["E02"] * (1 - v["E23"]) ** (t - 1))
    year0 = t == 0
    out = {}

    def add(oid, horizon, capex, supply_benefit, other, opex, supplier=_ZERO):
        out[oid] = _block(oid, horizon, capex, supply_benefit, other, opex, supplier, tl)

    add(
        "1", g02a,
        capex=np.where(year0, c["c_irr_cap"], 0.0) * grant
        + np.where((t > 0) & (t < g02a) & _mod0(t, v["I10"], warn, "pump life (I10)"), v["I03"] * v["I09"] * grant, 0.0),
        supply_benefit=benefit["1"],
        other=_ZERO,
        opex=_y0(v["I07"] * v["I03"] * v["I01"]),
        supplier=_y0(v["I03"] * v["I04"]),
    )
    add(
        "2", g02a,
        capex=np.where(_mod0(t, v["A13"], warn, "hive life (A13)") & (t < g02a), v["A08"] * v["A01"] * grant, 0.0),
        supply_benefit=benefit["2"],
        other=_y0(c["c_wax"] * max(0, v["A09"] - v["A05"])),
        opex=_ZERO,
        supplier=_y0(v["A08"] * v["A04"] * v["A02"] * v["A12"] + c["c_wax"] * v["A05"]),
    )
    add(
        "3", g02b,
        capex=(
            np.where(year0, v["R07"] * v["R01"], 0.0)
            + np.where(t <= v["R10"], _div(c["c_regen_ha"] * v["R02"], v["R10"] + 1, warn, "aftercare years (R10)"), 0.0)
        ) * grant,
        supply_benefit=benefit["3"],
        other=_y0(
            v["G08"] * c["c_regen_ha"] * v["R03"] * v["R08a"] * v["R08b"]
            * np.minimum(1, _div(t, v["R11"], warn, "carbon ramp (R11)"))
        ),
        opex=_ZERO,
    )
    add(
        "4", g02a,
        capex=np.where(year0, v["B03"] * grant, 0.0),
        supply_benefit=_ZERO,
        other=_y0(v["G08"] * c["c_char"] * v["B07"] * v["B06"] * (1 - v["B08"])),
        opex=_y0(c["c_char"] * v["B04"] + v["B01"] * v["B05"]),
    )
    add(
        "5", g02a,
        capex=np.where(_mod0(t, v["S03"], warn, "stove life (S03)") & (t < g02a), v["S05"] * v["S01"] * grant, 0.0),
        supply_benefit=benefit["5"],
        other=_y0(v["G08"] * v["S05"] * v["S10"] * v["S08"] * (1 - v["B08"])),
        opex=_ZERO,
        supplier=_y0(min(v["C01"], v["S05"] * v["S06"]) * v["S09"] * v["S02"] * v["S07"]),
    )
    add(
        "6", g02a,
        capex=np.where(year0, c["c_flood_cap"], 0.0),
        supply_benefit=_ZERO,
        other=_y0(v["H05"] * ((c["c_floss0"] - v["N05"] * c["c_rec0"]) - (c["c_floss1"] - v["N05"] * c["c_rec1"]))),
        opex=_y0(v["D08"] * c["c_flood_cap"]),
    )
    add(
        "7a", g02a,
        capex=np.where(year0, c["c_pv_cap"], 0.0) + np.where(t == v["E26"], v["E12"] * c["c_pv_cap"], 0.0),
        supply_benefit=_ZERO,
        other=_y0(pv_kwh * v["E03"]),
        opex=_y0(v["E22"] * c["c_pv_cap"]),
    )
    add(
        "7b", g02a,
        capex=np.where(year0, c["c_bat_cap"], 0.0) + np.where(t == v["E27"], v["E15"] * c["c_bat_kwh"] * v["E05"], 0.0),
        supply_benefit=_ZERO,
        other=_y0(c["c_bat_avoid"]),
        opex=_y0(v["E24"] * c["c_bat_cap"]),
    )
    add(
        "7c", g02a,
        capex=_ZERO,
        supply_benefit=_ZERO,
        other=_y0(pv_kwh * v["E03"] * v["E08"]),
        opex=_ZERO,
    )
    add(
        "7d", g02a,
        capex=np.where(year0 | (_mod0(t, v["E20"], warn, "genset life (E20)") & (t < g02a)), c["c_gen_cap"], 0.0),
        supply_benefit=_ZERO,
        other=_y0(c["c_gen_avoid"]),
        opex=_y0(c["c_gen_fuel"] + v["E21"] * c["c_gen_cap"]),
    )
    add(
        "8", g02a,
        capex=np.where(year0, v["M09"], 0.0),
        supply_benefit=benefit["8"],
        other=_y0(c["c_mango_margin"] - v["H01"] * c["c_sub_t"] * c["c_mango_mpt"]),
        opex=_y0(v["M11"]),
    )
    add(
        "9", g02a,
        capex=np.where(year0, v["F02"] * grant, 0.0),
        supply_benefit=benefit["9"],
        other=_ZERO,
        opex=_y0(v["F03"] * c["c_catch"] * v["F01"] * grant),
    )
    add(
        "10", g02a,
        capex=_ZERO,
        supply_benefit=_ZERO,
        other=_y0((1 - v["N05"]) * v["H05"] * c["c_rec0"]),
        opex=_y0((1 - v["N05"]) * c["c_premium"]),
    )
    return out


# --- Portfolio sheet -----------------------------------------------------------------------

def portfolio(v: dict, c: dict, sup: dict, opts: dict, include: dict, tl: dict | None = None) -> dict:
    """Portfolio sheet: the selected options combined, with the three interaction rows."""
    tl = tl or timeline(v)
    df = tl["df"]
    inc = {oid: float(bool(include.get(oid, False))) for oid in OPTION_IDS}

    def total(row):
        acc = np.zeros(N_YEARS)
        for oid in OPTION_IDS:
            if inc[oid]:
                acc = acc + opts[oid][row]
        return acc

    active = (YEARS <= v["G02a"]).astype(float)
    capex = total("capex")
    supply_benefit = sup["portfolio"]["benefit"]
    other = total("other")
    int_flood_ins = -inc["6"] * inc["10"] * (1 - v["N05"]) * _y0(v["H05"] * (c["c_rec0"] - c["c_rec1"]))
    int_bat_gen = -inc["7b"] * inc["7d"] * opts["7b"]["other"]
    int_ppa_pv = -inc["7a"] * inc["7c"] * opts["7c"]["other"]
    opex = total("opex")
    opflow = supply_benefit + other + int_flood_ins + int_bat_gen + int_ppa_pv - opex
    tax = tl["tax_rate"] * np.maximum(0, opflow)
    fcf = (opflow - capex - tax) * active
    supplier = total("supplier") * active

    rows = {
        "active": active,
        "capex": capex,
        "supply": supply_benefit,
        "other": other,
        "int_flood_ins": int_flood_ins,
        "int_bat_gen": int_bat_gen,
        "int_ppa_pv": int_ppa_pv,
        "opex": opex,
        "opflow": opflow,
        "tax": tax,
        "fcf": fcf,
        "supplier": supplier,
    }
    pv = {name: float(row @ df) for name, row in rows.items() if name != "active"}
    return {
        **rows,
        "include": {oid: bool(inc[oid]) for oid in OPTION_IDS},
        "pv": pv,  # column C, "NPV / total"
        "npv": pv["fcf"],  # F5 = p_npv
        "capex_pv": pv["capex"],  # F6
        "npv_with_suppliers": pv["fcf"] + pv["supplier"],  # F7 = p_npv_cons
    }


def npv_bridge(opts: dict, port: dict, sup: dict, tl: dict) -> list:
    """Exact walk from the sum of standalone option NPVs to the package NPV.

    A presentation of the Portfolio sheet, not extra model logic: the steps add up to
    `port["npv"]` to rounding. Each step is (key, label, value, kind).
    """
    df, active = tl["df"], port["active"]
    included = [oid for oid in OPTION_IDS if port["include"][oid]]
    steps = [(oid, OPTION_NAMES[oid], opts[oid]["npv"], "option") for oid in included]

    own_supply = sum((opts[oid]["supply"] for oid in included), np.zeros(N_YEARS))
    own_tax = sum((opts[oid]["tax"] for oid in included), np.zeros(N_YEARS))
    horizon = sum(
        ((opts[oid]["opflow"] - opts[oid]["capex"] - opts[oid]["tax"]) * (active - opts[oid]["active"]) for oid in included),
        np.zeros(N_YEARS),
    )
    adjustments = [
        ("supply_overlap", "Overlap in supply benefits", float(((port["supply"] - own_supply) * active) @ df)),
        ("int_flood_ins", "Flood works lower insurance recoveries", float((port["int_flood_ins"] * active) @ df)),
        ("int_bat_gen", "Battery redundant with genset", float((port["int_bat_gen"] * active) @ df)),
        ("int_ppa_pv", "PPA and owned PV are alternatives", float((port["int_ppa_pv"] * active) @ df)),
        ("tax", "Tax on the combined flow", float((-(port["tax"] - own_tax) * active) @ df)),
        ("horizon", "Package horizon applied to every option", float(horizon @ df)),
    ]
    steps += [(key, label, value, "adjustment") for key, label, value in adjustments]
    steps.append(("total", "Package NPV", port["npv"], "total"))
    return steps


# --- Results sheet -------------------------------------------------------------------------

def results(v: dict, c: dict, vc: dict, sup: dict, opts: dict, port: dict) -> dict:
    """Results sheet: one row per option, the selected portfolio and the key quantities."""
    npvs = [opts[oid]["npv"] for oid in OPTION_IDS]
    rows = []
    for oid in OPTION_IDS:
        o = opts[oid]
        npv, supply_pv = o["npv"], o["supply_pv"]
        total = npv + o["supplier_pv"]
        if supply_pv > 0:
            breakeven = "already positive" if npv >= 0 else (supply_pv - npv) / supply_pv
        else:
            breakeven = "n/a"
        if npv >= 0:
            reading = "Adds value on factory cash alone"
        elif total >= 0:
            reading = "Positive only with supplier income"
        else:
            reading = "Does not pay at these inputs"
        rows.append({
            "id": oid,
            "name": o["name"],
            "npv": npv,
            "capex_pv": o["capex_pv"],
            "supplier_pv": o["supplier_pv"],
            "npv_with_suppliers": total,
            "supply_pv": supply_pv,
            "breakeven": breakeven,
            "rank": 1 + sum(1 for other in npvs if other > npv),  # RANK(C, C5:C17)
            "reading": reading,
        })
    key = {
        "c_margin": c["c_margin"],
        "c_VST": c["c_VST"],
        "baseline_supply_loss_y1": float(sup["baseline"]["loss"][1]),
        "c_catch": c["c_catch"],
        "c_out_cost": c["c_out_cost"],
        "c_out_pct": c["c_out_pct"],
        "c_floss0": c["c_floss0"],
        "expected_flood_loss": v["H05"] * c["c_floss0"],
        "share_gh_weighted": vc["summary"]["share_gh"]["weighted"],
        "share_revenue_gh": vc["business"]["share_stays"],
        "units": vc["units"],
        "sales": vc["business"]["total_revenue"],
    }
    return {
        "options": rows,
        "portfolio_npv": port["npv"],
        "portfolio_npv_with_suppliers": port["npv_with_suppliers"],
        "key": key,
    }


# --- Checks sheet --------------------------------------------------------------------------

def checks(v: dict, c: dict, vc: dict, sup: dict, port: dict, units: dict | None = None) -> list:
    """Checks sheet: the ten model checks. `units` (input id -> unit) is needed for check 3."""
    inc = port["include"]
    out = []

    def add(name, value, status, guards):
        out.append({"check": name, "value": value, "status": status, "guards": guards})

    def ok(passed):
        return "OK" if passed else "CHECK"

    pct = c["c_out_pct"]
    add("Outage cost within 0–10% of sales", pct, ok(0 <= pct <= 0.1),
        "Outage losses out of line with what Ghanaian firms report (WBES 2023)")
    over = c["c_rec0"] - c["c_floss0"]
    add("Insurance recovery ≤ flood loss", over, ok(over <= 0), "Recovering more than the loss")
    if units is None:
        add("All shares between 0 and 1", float("nan"), "n/a (units not supplied)", "A percentage input outside 0–100%")
    else:
        bad = sum(1 for iid, unit in units.items() if unit.startswith("%") and (v[iid] < 0 or v[iid] > 1))
        add("All shares between 0 and 1", bad, ok(bad == 0), "A percentage input outside 0–100%")
    decline = min(sup["options"]["5"]["d"], sup["options"]["9"]["d"], sup["portfolio"]["d"])
    add("Decline rate stays ≥ 0 with options", decline, ok(decline >= 0), "Options cutting tree loss below zero")
    margin = vc["amazon"]["lines"][-1]["usd"]
    add("Brand margin per Amazon jar is positive", margin, ok(margin > 0), "Amazon channel losing money per jar")
    gap = vc["amazon"]["price"] - v["V01"]
    add("Value chain sums to retail price", gap, ok(abs(gap) < 0.005), "Missing or double-counted cost lines")
    surplus = v["E09"] * v["E02"] - v["C08"] * 1000
    add("PV output ≤ factory electricity use", surplus, ok(surplus <= 0), "Counting exported solar as savings")
    overlap = port["pv"]["int_bat_gen"]
    add("Battery and genset not both counted", overlap, ok(not (inc["7b"] and inc["7d"]) or overlap < 0),
        "Double-counting outage protection")
    selected = sum(1 for oid in OPTION_IDS if inc[oid])
    add("Portfolio with nothing selected returns zero", selected, "n/a (options selected)" if selected > 0 else "OK",
        "Engine produces value from nothing")
    add("Carbon off in base case", v["G08"], "OK" if v["G08"] == 0 else "NOTE: carbon on",
        "Counting unsigned carbon revenue")
    return out


# --- everything in one call ----------------------------------------------------------------

def run_model(v: dict, include: dict, units: dict | None = None, curves: dict = JRC_CURVES) -> dict:
    """Run the whole workbook for one set of active input values and include switches."""
    warn: list = []
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        tl = timeline(v)
        vc = value_chain(v, warn)
        c = calc(v, vc, curves, warn)
        sup = supply(v, c, include, warn)
        opts = option_cashflows(v, c, sup, tl, warn)
        port = portfolio(v, c, sup, opts, include, tl)
        res = results(v, c, vc, sup, opts, port)
        chk = checks(v, c, vc, sup, port, units)
    if not math.isfinite(port["npv"]) or not all(math.isfinite(o["npv"]) for o in opts.values()):
        warn.append("A result is not a finite number: Excel would show an error for these inputs.")
    return {
        "timeline": tl,
        "value_chain": vc,
        "calc": c,
        "supply": sup,
        "options": opts,
        "portfolio": port,
        "results": res,
        "checks": chk,
        "warnings": list(dict.fromkeys(warn)),
    }
