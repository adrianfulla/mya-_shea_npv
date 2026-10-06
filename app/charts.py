"""Number formatting and Plotly figure builders for the app. No Streamlit, no model logic.

Palette: neutral greys plus one accent. Green and red are used only for good versus bad
(NPV sign, value staying in versus leaving Ghana). All colours clear 3:1 contrast on both
the light and the dark Streamlit surface; backgrounds are transparent and text is left to
the theme, so the same figure works in either.
"""
from __future__ import annotations

import math

import numpy as np
import plotly.graph_objects as go

GREEN = "#008300"
RED = "#e66767"
ACCENT = "#2a78d6"
GREY = "#898781"
GREY_DARK = "#6b6a65"
GREY_LIGHT = "#b9b7ae"
MINUS = "−"
ARROW = "→"

SHORT = {
    "1": "1 Solar irrigation",
    "2": "2 Apiaries",
    "3": "3 Regeneration",
    "4": "4 Biochar",
    "5": "5 Processing stoves",
    "6": "6 Flood drainage",
    "7a": "7a Solar PV",
    "7b": "7b Battery + islanding",
    "7c": "7c Solar PPA",
    "7d": "7d Diesel genset",
    "8": "8 Mango toll pilot",
    "9": "9 Fire management",
    "10": "10 Insurance",
}


# --- formatting ----------------------------------------------------------------------------

def _signed(text: str, value: float, plus: bool = False) -> str:
    if value < 0:
        return MINUS + text
    return ("+" + text) if plus and value > 0 else text


def usd(x, decimals: int = 0, plus: bool = False) -> str:
    """$ with thousands separators; negatives with a minus sign: −$135,112."""
    if x is None or not math.isfinite(x):
        return "n/a"
    x = round(x, decimals) + 0.0
    return _signed(f"${abs(x):,.{decimals}f}", x, plus)


def usd_short(x, plus: bool = False) -> str:
    """Compact money for titles and labels: $260k, −$1.27M, $950."""
    if x is None or not math.isfinite(x):
        return "n/a"
    a = abs(x)
    if a >= 999_500:
        text = f"${a / 1e6:.2f}M"
    elif a >= 1_000:
        text = f"${a / 1e3:,.0f}k"
    else:
        text = f"${a:,.0f}"
        x = round(x) + 0.0
    return _signed(text, x, plus)


def pct(x, decimals: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x * 100:.{decimals}f}%".replace("-", MINUS)


def number(x, decimals: int = 0) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:,.{decimals}f}".replace("-", MINUS)


# --- shared layout -------------------------------------------------------------------------

def _layout(fig: go.Figure, height: int, right: int = 20, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=right, t=36 if legend else 10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text=""),
        bargap=0.35,
        hovermode="closest",
    )
    return fig


def _money_axis(axis_update, title: str = ""):
    axis_update(tickformat="$~s", zeroline=True, zerolinecolor=GREY, zerolinewidth=1, title_text=title)


def _text_column(fig: go.Figure, x: float, rows, header: str, top: float):
    """A column of text to the right of the plot (paper x), one line per (y, text)."""
    fig.add_annotation(xref="paper", x=x, y=top, yref="y", text=f"<b>{header}</b>", showarrow=False,
                       xanchor="right", align="right")
    for y, text in rows:
        fig.add_annotation(xref="paper", x=x, y=y, yref="y", text=text, showarrow=False, xanchor="right",
                           align="right")


# --- Overview: NPV by option with benign -> stress range ---------------------------------------

def npv_by_option(rows) -> go.Figure:
    """rows: dicts with label, npv, benign, stress, reading. Highest NPV at the top."""
    rows = sorted(rows, key=lambda r: r["npv"])
    n = len(rows)
    pos = list(range(n))
    fig = go.Figure()
    for name, color, keep in (
        ("Factory NPV: adds value", GREEN, lambda r: r["npv"] >= 0),
        ("Factory NPV: loses value", RED, lambda r: r["npv"] < 0),
    ):
        part = [(p, r) for p, r in zip(pos, rows) if keep(r)]
        fig.add_bar(
            orientation="h",
            y=[p + 0.12 for p, _ in part],
            x=[r["npv"] for _, r in part],
            width=0.44,
            marker_color=color,
            name=name,
            customdata=[[r["label"], usd(r["npv"]), r["reading"]] for _, r in part],
            hovertemplate="<b>%{customdata[0]}</b><br>Factory NPV %{customdata[1]}<br>%{customdata[2]}<extra></extra>",
        )
    xs, ys = [], []
    for p, r in zip(pos, rows):
        xs += [r["benign"], r["stress"], None]
        ys += [p - 0.25, p - 0.25, None]
    fig.add_scatter(x=xs, y=ys, mode="lines", line=dict(color=GREY, width=2), name="Range", hoverinfo="skip",
                    showlegend=False)
    fig.add_scatter(
        x=[r["benign"] for r in rows], y=[p - 0.25 for p in pos], mode="markers", name="Benign scenario",
        marker=dict(symbol="circle-open", size=9, color=GREY, line=dict(width=2, color=GREY)),
        customdata=[[r["label"], usd(r["benign"])] for r in rows],
        hovertemplate="<b>%{customdata[0]}</b><br>Benign scenario %{customdata[1]}<extra></extra>",
    )
    fig.add_scatter(
        x=[r["stress"] for r in rows], y=[p - 0.25 for p in pos], mode="markers", name="Stress scenario",
        marker=dict(symbol="circle", size=9, color=GREY),
        customdata=[[r["label"], usd(r["stress"])] for r in rows],
        hovertemplate="<b>%{customdata[0]}</b><br>Stress scenario %{customdata[1]}<extra></extra>",
    )
    _text_column(fig, 0.80, [(p, usd_short(r["npv"])) for p, r in zip(pos, rows)], "NPV now", n - 0.15)
    _text_column(fig, 1.0, [(p, f"{usd_short(r['benign'])} {ARROW} {usd_short(r['stress'])}") for p, r in zip(pos, rows)],
                 f"Benign {ARROW} stress", n - 0.15)
    _layout(fig, height=40 * n + 110)
    fig.update_layout(barcornerradius=3, barmode="overlay")
    fig.update_xaxes(domain=[0, 0.70])
    _money_axis(fig.update_xaxes, "Factory NPV (USD)")
    fig.update_yaxes(tickmode="array", tickvals=pos, ticktext=[r["label"] for r in rows], range=[-0.7, n + 0.2],
                     showgrid=False, zeroline=False)
    return fig


# --- Package builder: waterfall and cash flows ---------------------------------------------------

def package_waterfall(steps) -> go.Figure:
    """steps: (key, label, value, kind) from engine.npv_bridge, zero adjustments already removed."""
    labels, bases, deltas, colors, texts = [], [], [], [], []
    running = 0.0
    for _key, label, value, kind in steps:
        labels.append(label)
        if kind == "total":
            bases.append(0.0)
            deltas.append(value)
            colors.append(ACCENT)
            texts.append(usd_short(value))
        else:
            bases.append(running)
            deltas.append(value)
            colors.append(GREY if kind == "adjustment" else (GREEN if value >= 0 else RED))
            texts.append(usd_short(value, plus=True))
            running += value
    n = len(steps)
    fig = go.Figure()
    fig.add_bar(
        orientation="h", y=list(range(n)), x=deltas, base=bases, width=0.5, marker_color=colors,
        customdata=[[label, text] for label, text in zip(labels, texts)],
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>",
    )
    _text_column(fig, 1.0, [(i, t) for i, t in enumerate(texts)], "NPV", -0.9)
    _layout(fig, height=34 * n + 90, legend=False)
    fig.update_xaxes(domain=[0, 0.88])
    _money_axis(fig.update_xaxes, "Cumulative NPV (USD)")
    fig.update_yaxes(tickmode="array", tickvals=list(range(n)), ticktext=labels, autorange=False,
                     range=[n - 0.4, -1.3], showgrid=False, zeroline=False)
    return fig


def cashflow_chart(years, benefits, opex, capex, tax, fcf) -> go.Figure:
    """Stacked annual flows (benefits up; opex, capex and tax down) with the FCF line. One USD axis."""
    fig = go.Figure()
    for name, series, color, sign in (
        ("Benefits", benefits, GREY, 1),
        ("Opex", opex, GREY_LIGHT, -1),
        ("Capex", capex, GREY_DARK, -1),
        ("Tax", tax, "#d9d7cf", -1),
    ):
        fig.add_bar(x=years, y=[sign * v for v in series], name=name, marker_color=color,
                    customdata=[usd(sign * v) for v in series],
                    hovertemplate=f"Year %{{x}}<br>{name} %{{customdata}}<extra></extra>")
    fig.add_scatter(x=years, y=fcf, mode="lines+markers", name="Free cash flow",
                    line=dict(color=ACCENT, width=2), marker=dict(size=7, color=ACCENT),
                    customdata=[usd(v) for v in fcf],
                    hovertemplate="Year %{x}<br>Free cash flow %{customdata}<extra></extra>")
    _layout(fig, height=380)
    fig.update_layout(barmode="relative", bargap=0.3, hovermode="x unified")
    fig.update_xaxes(title_text="Year", dtick=2, showgrid=False)
    _money_axis(fig.update_yaxes, "USD per year (constant 2026)")
    return fig


# --- Value chain ---------------------------------------------------------------------------------

def value_chain_waterfall(channel: dict) -> go.Figure:
    """From the retail price down to the brand margin, each line split into stays / leaves Ghana."""
    lines = channel["lines"]
    price = channel["price"]
    labels = ["Retail price"] + [l["label"] for l in lines]
    n = len(labels)
    stay_base, stay_w, leave_base, leave_w = [None], [None], [None], [None]
    remaining = price
    for line in lines[:-1]:
        end = remaining - line["usd"]
        stay_base.append(end)
        stay_w.append(line["stays"])
        leave_base.append(end + line["stays"])
        leave_w.append(line["leaves"])
        remaining = end
    margin = lines[-1]["usd"]  # what is left after every cost line
    stay_base.append(min(0.0, margin))
    stay_w.append(abs(margin))
    leave_base.append(None)
    leave_w.append(None)

    hover = [[labels[0], usd(price, 2), "100.0%", ""]] + [
        [l["label"], usd(l["usd"], 2), pct(l["pct"]), f"{pct(l['gh_share'], 0)} stays in Ghana"] for l in lines
    ]
    template = "<b>%{customdata[0]}</b><br>%{customdata[1]} per jar, %{customdata[2]} of price<br>%{customdata[3]}<extra></extra>"
    fig = go.Figure()
    fig.add_bar(orientation="h", y=[0], x=[price], base=[0], width=0.55, marker_color=GREY, name="Retail price",
                customdata=hover[:1], hovertemplate=template)
    fig.add_bar(orientation="h", y=list(range(n)), x=stay_w, base=stay_base, width=0.55, marker_color=GREEN,
                name="Stays in Ghana", customdata=hover, hovertemplate=template)
    fig.add_bar(orientation="h", y=list(range(n)), x=leave_w, base=leave_base, width=0.55, marker_color=RED,
                name="Leaves Ghana", customdata=hover, hovertemplate=template)
    _text_column(fig, 0.89, [(0, usd(price, 2))] + [(i, usd(l["usd"], 2)) for i, l in enumerate(lines, start=1)],
                 "USD per jar", -0.9)
    _text_column(fig, 1.0, [(0, "100.0%")] + [(i, pct(l["pct"])) for i, l in enumerate(lines, start=1)],
                 "% of price", -0.9)
    _layout(fig, height=30 * n + 100)
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(domain=[0, 0.78], tickformat="$,.2f", title_text="USD per jar", zeroline=True,
                     zerolinecolor=GREY, zerolinewidth=1)
    fig.update_yaxes(tickmode="array", tickvals=list(range(n)), ticktext=labels, autorange=False,
                     range=[n - 0.4, -1.3], showgrid=False, zeroline=False)
    return fig


def export_share_line(shares, stays, current_share: float, current_stays: float) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=shares, y=stays, mode="lines", line=dict(color=ACCENT, width=2), name="Share staying in Ghana",
                    hovertemplate="Export share %{x:.0%}<br>Stays in Ghana %{y:.1%}<extra></extra>")
    fig.add_scatter(x=[current_share], y=[current_stays], mode="markers", name="Current export share (G05)",
                    marker=dict(size=11, color=ACCENT, line=dict(width=2, color="rgba(255,255,255,0.9)")),
                    hovertemplate="Current export share %{x:.0%}<br>Stays in Ghana %{y:.1%}<extra></extra>")
    # The curve falls from left to right: the free space is above-right of the point, or below-left of it.
    above = current_share < 0.4
    fig.add_annotation(x=current_share, y=current_stays, text=f"Now: {pct(current_share, 0)} exports {ARROW} {pct(current_stays)}",
                       showarrow=False, yshift=18 if above else -18, xshift=8 if above else -8,
                       xanchor="left" if above else "right")
    _layout(fig, height=340)
    fig.update_xaxes(tickformat=".0%", title_text="Export share of sales (G05)", range=[-0.02, 1.02])
    fig.update_yaxes(tickformat=".0%", title_text="Share of jar price staying in Ghana", rangemode="tozero")
    return fig


# --- Sensitivity ---------------------------------------------------------------------------------

def tornado_chart(rows, labels: dict) -> go.Figure:
    """rows: sensitivity.tornado output (largest swing first). Bars run from the base NPV."""
    n = len(rows)
    base = rows[0]["base_npv"] if rows else 0.0
    ys = list(range(n))
    fig = go.Figure()
    for key, name, color in (("npv_low", "Input at Low", GREY), ("npv_high", "Input at High", ACCENT)):
        fig.add_bar(
            orientation="h", y=ys, x=[r[key] - base for r in rows], base=[base] * n, width=0.5, marker_color=color,
            name=name,
            customdata=[[labels[r["id"]], usd(r[key]), usd(r[key] - base, plus=True)] for r in rows],
            hovertemplate=f"<b>%{{customdata[0]}}</b><br>{name}: package NPV %{{customdata[1]}}"
                          "<br>%{customdata[2]} versus now<extra></extra>",
        )
    fig.add_vline(x=base, line_width=1, line_color=GREY)
    _text_column(fig, 1.0, [(i, usd_short(r["swing"])) for i, r in enumerate(rows)], "Swing", -0.9)
    _layout(fig, height=30 * n + 110)
    fig.update_layout(barmode="overlay", barcornerradius=3)
    fig.update_xaxes(domain=[0, 0.88])
    _money_axis(fig.update_xaxes, "Package NPV (USD)")
    fig.update_xaxes(zeroline=False)
    fig.update_yaxes(tickmode="array", tickvals=ys, ticktext=[labels[r["id"]] for r in rows], autorange=False,
                     range=[n - 0.4, -1.3], showgrid=False, zeroline=False)
    return fig


# --- Optimal package: frontier and Monte Carlo ---------------------------------------------------

def frontier_chart(rows, short: dict = SHORT) -> go.Figure:
    """Best NPV against the capex budget, as a step line. Each step is labelled with what enters."""
    rows = [r for r in rows if r["package"] is not None]
    xs = [r["budget"] for r in rows]
    ys = [r["package"]["objective"] for r in rows]
    fig = go.Figure()
    fig.add_scatter(
        x=xs, y=ys, mode="lines", line=dict(color=ACCENT, width=2, shape="hv"), name="Best NPV within the budget",
        customdata=[[usd(r["budget"]), usd(r["package"]["objective"]), " + ".join(short[o] for o in r["package"]["ids"]) or "No options"]
                    for r in rows],
        hovertemplate="Budget %{customdata[0]}<br>Best NPV %{customdata[1]}<br>%{customdata[2]}<extra></extra>",
    )
    steps = [r for i, r in enumerate(rows) if i == 0 or r["entered"] or r["left"]]
    fig.add_scatter(
        x=[r["budget"] for r in steps], y=[r["package"]["objective"] for r in steps], mode="markers",
        marker=dict(size=9, color=ACCENT, line=dict(width=2, color="rgba(255,255,255,0.9)")), name="Package changes",
        hoverinfo="skip",
    )
    for k, row in enumerate(steps):
        entered = [short[o] for o in row["entered"]]
        left = [short[o] for o in row["left"]]
        text = " + ".join(entered) if entered else ""
        if k == 0:
            text = "Start: " + (text or "no options")
        elif entered:
            text = "+ " + text
        if left:
            text += (" " if text else "") + f"({MINUS} {', '.join(left)})"
        late = xs[-1] > 0 and row["budget"] > 0.7 * xs[-1]  # near the right edge: put the label to the left
        fig.add_annotation(x=row["budget"], y=row["package"]["objective"], text=text, showarrow=False,
                           xanchor="right" if late else "left", yanchor="top", xshift=-8 if late else 6,
                           yshift=-3 - 15 * (k % 2))
    _layout(fig, height=420)
    fig.update_xaxes(tickformat="$~s", title_text="Capex budget (present value, USD)", rangemode="tozero")
    _money_axis(fig.update_yaxes, "Best NPV (USD)")
    fig.update_yaxes(rangemode="tozero")
    return fig


def inclusion_chart(inclusion: dict, short: dict = SHORT) -> go.Figure:
    """Share of Monte Carlo draws in which each option is in the optimal package."""
    order = sorted(inclusion, key=lambda o: inclusion[o])
    n = len(order)

    def band(share):
        return ACCENT if share >= 0.8 else GREY_LIGHT if share <= 0.2 else GREY

    fig = go.Figure()
    for name, color, keep in (("Robust yes (80% or more)", ACCENT, lambda s: s >= 0.8),
                              ("In between", GREY, lambda s: 0.2 < s < 0.8),
                              ("Robust no (20% or less)", GREY_LIGHT, lambda s: s <= 0.2)):
        part = [(i, o) for i, o in enumerate(order) if keep(inclusion[o])]
        fig.add_bar(orientation="h", y=[i for i, _ in part], x=[inclusion[o] for _, o in part], width=0.55,
                    marker_color=color, name=name,
                    customdata=[[short[o], pct(inclusion[o])] for _, o in part],
                    hovertemplate="<b>%{customdata[0]}</b><br>In the best package in %{customdata[1]} of draws<extra></extra>")
    for x in (0.2, 0.8):
        fig.add_vline(x=x, line_width=1, line_color=GREY)
    _text_column(fig, 1.0, [(i, pct(inclusion[o], 0)) for i, o in enumerate(order)], "Share of draws", n - 0.3)
    _layout(fig, height=30 * n + 110)
    fig.update_layout(barmode="overlay", barcornerradius=3)
    fig.update_xaxes(domain=[0, 0.88], range=[0, 1], tickformat=".0%", title_text="Share of draws in which the option is in the best package")
    fig.update_yaxes(tickmode="array", tickvals=list(range(n)), ticktext=[short[o] for o in order], range=[-0.7, n + 0.2],
                     showgrid=False, zeroline=False)
    return fig


def npv_histogram(values, p10: float, p50: float, p90: float, now: float, bins: int = 40) -> go.Figure:
    """Distribution of a package's NPV over the draws, with P10, P50, P90 and today's value marked."""
    counts, edges = np.histogram(values, bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2
    width = edges[1] - edges[0]
    share = counts / max(1, len(values))
    fig = go.Figure()
    for name, color, keep in (("NPV below zero", RED, centres < 0), ("NPV of zero or more", GREY, centres >= 0)):
        if keep.any():
            fig.add_bar(x=centres[keep], y=share[keep], width=width * 0.92, marker_color=color, name=name,
                        customdata=[[usd_short(a), usd_short(b), pct(s)] for a, b, s in
                                    zip(edges[:-1][keep], edges[1:][keep], share[keep])],
                        hovertemplate="%{customdata[0]} to %{customdata[1]}<br>%{customdata[2]} of draws<extra></extra>")
    top = float(share.max()) if len(share) else 1.0
    for label, x, anchor in (("P10", p10, "right"), ("P50", p50, "center"), ("P90", p90, "left")):
        fig.add_vline(x=x, line_width=2, line_color=ACCENT)
        fig.add_annotation(x=x, y=top * 1.16, text=f"<b>{label}</b> {usd_short(x)}", showarrow=False, xanchor=anchor,
                           yanchor="bottom")
    fig.add_vline(x=now, line_width=1, line_color=GREY_DARK)
    fig.add_annotation(x=now, y=top * 1.04, text=f"At current inputs {usd_short(now)}", showarrow=False,
                       xanchor="center", yanchor="bottom")
    _layout(fig, height=400)
    fig.update_layout(bargap=0, barmode="overlay", margin_t=60)
    _money_axis(fig.update_xaxes, "NPV of the package (USD)")
    fig.update_yaxes(tickformat=".0%", title_text="Share of draws", range=[0, top * 1.32])
    return fig


def spearman_chart(rows, labels: dict) -> go.Figure:
    """Rank correlation between each sampled input and the optimal NPV, strongest first."""
    n = len(rows)
    ys = list(range(n))
    fig = go.Figure()
    for name, color, keep in (("Higher input, higher NPV", ACCENT, lambda r: r["rho"] >= 0),
                              ("Higher input, lower NPV", GREY, lambda r: r["rho"] < 0)):
        part = [(i, r) for i, r in enumerate(rows) if keep(r)]
        fig.add_bar(orientation="h", y=[i for i, _ in part], x=[r["rho"] for _, r in part], width=0.5,
                    marker_color=color, name=name,
                    customdata=[[labels[r["id"]], f"{r['rho']:+.2f}".replace("-", MINUS)] for _, r in part],
                    hovertemplate="<b>%{customdata[0]}</b><br>Rank correlation %{customdata[1]}<extra></extra>")
    _text_column(fig, 1.0, [(i, f"{r['rho']:+.2f}".replace("-", MINUS)) for i, r in enumerate(rows)], "Correlation", -0.9)
    _layout(fig, height=30 * n + 110)
    fig.update_layout(barmode="overlay", barcornerradius=3)
    reach = max([abs(r["rho"]) for r in rows] + [0.1]) * 1.15
    fig.update_xaxes(domain=[0, 0.88], range=[-reach, reach], zeroline=True, zerolinecolor=GREY, zerolinewidth=1,
                     title_text="Spearman rank correlation with the optimal NPV")
    fig.update_yaxes(tickmode="array", tickvals=ys, ticktext=[labels[r["id"]] for r in rows], autorange=False,
                     range=[n - 0.4, -1.3], showgrid=False, zeroline=False)
    return fig
