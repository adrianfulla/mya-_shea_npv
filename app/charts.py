"""Number formatting and Plotly figure builders for the app. No Streamlit, no model logic.

Palette: neutral greys plus one accent. Green and red are used only for good versus bad
(NPV sign, value staying in versus leaving Ghana). All colours clear 3:1 contrast on both
the light and the dark Streamlit surface; backgrounds are transparent and text is left to
the theme, so the same figure works in either.
"""
from __future__ import annotations

import math

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
