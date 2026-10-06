"""Plain-language summary of the optimal package, built only from computed results. No Streamlit."""
from __future__ import annotations

from charts import pct, usd_short


def _join(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def summary(best, current, settings, labels: dict, value_text, input_name, monte_carlo=None, switching=None) -> list:
    """Sentences describing the optimum, how firm it is and what would change it.

    best, current: package dicts (`current` is the Package builder selection; None if unknown).
    labels: option id -> name. value_text(input id, value) -> "250 h/yr". input_name(id) -> name.
    monte_carlo: result of robustness.summarise with the optimum tracked as "optimum", or None.
    switching: sorted switching-value rows, or None if not computed.
    """
    if best is None:
        return ["No package meets the budget and the forced options."]
    metric = "factory NPV" if settings.objective == "factory NPV" else "factory plus supplier NPV"
    package = " + ".join(labels[o] for o in best["ids"]) if best["ids"] else "to fund none of the options"
    limits = []
    if settings.budget is not None:
        limits.append(f"a capex budget of {usd_short(settings.budget)}")
    if settings.forced_in:
        limits.append(f"{_join(labels[o] for o in settings.forced_in)} forced in")
    if settings.forced_out:
        limits.append(f"{_join(labels[o] for o in settings.forced_out)} forced out")
    within = f", given {_join(limits)}," if limits else ""
    sentences = [f"The best package at current inputs{within} is {package} ({metric} {usd_short(best['objective'])})."]

    if current is not None:
        gain = best["objective"] - current["objective"]
        if current["code"] == best["code"]:
            sentences.append("That is the package selected in Package builder.")
        elif gain >= 0:
            sentences.append(f"It is worth {usd_short(gain)} more than the Package builder selection.")
        else:
            sentences.append(f"The Package builder selection is worth {usd_short(-gain)} more, but it does not meet "
                             "these limits.")

    if monte_carlo is not None:
        share = monte_carlo["hold_share"]["optimum"]
        firm = [(o, f) for o, f in monte_carlo["inclusion"].items() if f >= 0.8]
        firm.sort(key=lambda item: -item[1])
        text = f"It is the best package in {pct(share, 0)} of {monte_carlo['n']:,} Monte Carlo draws"
        if firm:
            text += f"; {_join(f'{labels[o]} ({pct(f, 0)})' for o, f in firm)} " \
                    f"{'is' if len(firm) == 1 else 'are'} in the best package in at least 80% of draws"
        sentences.append(text + ".")

    if switching is not None:
        if not switching:
            sentences.append("No single input changes the recommendation inside its scanned range.")
        else:
            row = switching[0]
            moves = []
            if row["leaves"]:
                moves.append(f"{_join(labels[o] for o in row['leaves'])} {'drops' if len(row['leaves']) == 1 else 'drop'} out")
            if row["enters"]:
                moves.append(f"{_join(labels[o] for o in row['enters'])} {'enters' if len(row['enters']) == 1 else 'enter'}")
            direction = "rises above" if row["side"] == "above" else "falls below"
            sentences.append(
                f"The decision is most sensitive to {input_name(row['id'])}: if it {direction} "
                f"{value_text(row['id'], row['threshold'])} (now {value_text(row['id'], row['current'])}), "
                f"{_join(moves)}."
            )
    return sentences
