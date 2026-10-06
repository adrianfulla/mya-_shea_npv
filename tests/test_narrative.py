"""The plain-language summary is assembled from computed results only."""
import narrative
from model.robustness import Settings

LABELS = {"7a": "7a Solar PV", "7d": "7d Diesel genset", "8": "8 Mango toll pilot", "10": "10 Insurance", "6": "6 Flood"}


def package(code, ids, objective):
    return {"code": code, "ids": ids, "objective": objective}


def value_text(iid, x):
    return f"{x:g} h/yr"


def name(iid):
    return f"grid outage hours ({iid})"


def test_summary_states_the_package_and_the_gain():
    best, current = package(1, ("7a", "7d"), 300_000.0), package(2, ("7d",), 250_000.0)
    text = narrative.summary(best, current, Settings(), LABELS, value_text, name)
    assert text == ["The best package at current inputs is 7a Solar PV + 7d Diesel genset (factory NPV $300k).",
                    "It is worth $50k more than the Package builder selection."]
    same = narrative.summary(best, best, Settings(), LABELS, value_text, name)
    assert same[1] == "That is the package selected in Package builder."


def test_summary_names_the_limits():
    settings = Settings("factory + supplier NPV", 50_000.0, ("6",), ("7d", "8"))
    best, current = package(1, ("6",), 10_000.0), package(2, ("7d",), 90_000.0)
    first, second = narrative.summary(best, current, settings, LABELS, value_text, name)
    assert "given a capex budget of $50k, 6 Flood forced in and 7d Diesel genset and 8 Mango toll pilot forced out," in first
    assert "factory plus supplier NPV $10k" in first
    assert second == "The Package builder selection is worth $80k more, but it does not meet these limits."
    assert narrative.summary(None, current, settings, LABELS, value_text, name) == [
        "No package meets the budget and the forced options."]


def test_summary_adds_monte_carlo_and_the_closest_switching_value():
    best = package(1, ("7a", "7d", "8"), 300_000.0)
    mc = {"n": 1000, "hold_share": {"optimum": 0.515}, "inclusion": {"7a": 0.93, "7d": 1.0, "8": 0.5, "10": 0.1, "6": 0.0}}
    rows = [{"id": "H08", "side": "below", "threshold": 120.0, "current": 250.0, "leaves": ("7d",), "enters": ("10",)}]
    text = narrative.summary(best, None, Settings(), LABELS, value_text, name, mc, rows)
    assert text[1] == ("It is the best package in 52% of 1,000 Monte Carlo draws; 7d Diesel genset (100%) and "
                       "7a Solar PV (93%) are in the best package in at least 80% of draws.")
    assert text[2] == ("The decision is most sensitive to grid outage hours (H08): if it falls below 120 h/yr "
                       "(now 250 h/yr), 7d Diesel genset drops out and 10 Insurance enters.")
    none = narrative.summary(best, None, Settings(), LABELS, value_text, name, None, [])
    assert none[-1] == "No single input changes the recommendation inside its scanned range."
    above = narrative.summary(best, None, Settings(), LABELS, value_text, name, None,
                              [dict(rows[0], side="above", leaves=(), enters=("6", "10"))])
    assert above[-1].endswith("if it rises above 120 h/yr (now 250 h/yr), 6 Flood and 10 Insurance enter.")
