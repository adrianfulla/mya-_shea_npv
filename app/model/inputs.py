"""Workbook loader and input state.

Reads the Inputs register (and the display sheets) from the xlsx and reproduces the
Low/Base/High case logic and the hazard-scenario switch. No Streamlit in here.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

DEFAULT_WORKBOOK = Path(__file__).resolve().parents[1] / "data" / "Shea_Resilience_NPV_Model.xlsx"

SCENARIO_ID = "G09"
SCENARIO_NAMES = {1: "Benign", 2: "Base", 3: "Stress"}
SCENARIO_CASE = "S"  # case code for rows whose case follows the scenario switch
OPTION_IDS = ("1", "2", "3", "4", "5", "6", "7a", "7b", "7c", "7d", "8", "9", "10")

_FIRST_INPUT_ROW = 5
_CSV_FIELDS = ("ID", "Input", "Unit", "Low", "Base", "High", "Case", "Custom", "Active")


@dataclass(frozen=True)
class Source:
    label: str
    url: str
    quote: str


@dataclass(frozen=True)
class InputSpec:
    id: str
    row: int
    group: str
    name: str
    unit: str
    low: float
    base: float
    high: float
    case: str  # "L", "B", "H" or "S" (follows the hazard scenario)
    confidence: str
    verdict: str
    rationale: str
    correction: str
    sources: tuple[Source, ...]

    @property
    def follows_scenario(self) -> bool:
        return self.case == SCENARIO_CASE

    @property
    def is_switch(self) -> bool:
        return self.unit == "switch"

    @property
    def is_percent(self) -> bool:
        return is_percent_unit(self.unit)

    @property
    def display_scale(self) -> float:
        """Stored value x display_scale = the number shown to people (fractions shown as %)."""
        return 100.0 if self.is_percent else 1.0


def is_percent_unit(unit: str) -> bool:
    """Units stored as fractions that read better as percentages."""
    return unit.startswith("%") or unit.startswith("pts") or unit == "/yr"


@dataclass(frozen=True)
class WorkbookData:
    path: str
    inputs: tuple[InputSpec, ...]
    curves: dict  # {"depth": [...], "africa": [...], "global": [...]}
    include: dict  # option id -> bool (Portfolio!C5:C17)
    option_names: dict  # option id -> full name (Portfolio!A5:A17)
    gates: tuple[dict, ...]
    audit_problems: tuple[dict, ...]
    audit_spotcheck: tuple[dict, ...]
    sources: tuple[dict, ...]
    sens_scenarios: tuple[dict, ...]
    sens_tornado: tuple[dict, ...]
    readme: tuple[tuple[str, str], ...]

    @property
    def by_id(self) -> dict:
        return {s.id: s for s in self.inputs}

    @property
    def units(self) -> dict:
        return {s.id: s.unit for s in self.inputs}

    @property
    def sensitivity_ids(self) -> list:
        ids = self.by_id
        return [r["id"] for r in self.sens_tornado if r["id"] in ids]


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def load_workbook_data(path: str | Path = DEFAULT_WORKBOOK) -> WorkbookData:
    """Load the workbook twice (formulas and cached values) and pull out everything the app needs."""
    path = Path(path)
    wb_f = openpyxl.load_workbook(path)
    wb_v = openpyxl.load_workbook(path, data_only=True)

    def cell(sheet: str, row: int, col: int):
        """Typed value, or the cached value when the cell holds a formula."""
        value = wb_f[sheet].cell(row, col).value
        if isinstance(value, str) and value.startswith("="):
            return wb_v[sheet].cell(row, col).value
        return value

    # --- Inputs register ---------------------------------------------------------------
    ws = wb_f["Inputs"]
    specs = []
    for row in range(_FIRST_INPUT_ROW, ws.max_row + 1):
        iid = _text(ws.cell(row, 1).value)
        if not iid:
            continue
        case_cell = ws.cell(row, 8).value
        if isinstance(case_cell, str) and case_cell.startswith("="):
            if "CHOOSE(in_G09" not in case_cell.replace(" ", ""):
                raise ValueError(f"Inputs!H{row}: unsupported case formula {case_cell!r}")
            case = SCENARIO_CASE
        else:
            case = _text(case_cell).upper() or "B"
            if case not in ("L", "B", "H"):
                raise ValueError(f"Inputs!H{row}: case must be L, B or H, got {case_cell!r}")
        sources = []
        for col in (14, 17, 20):
            label = _text(cell("Inputs", row, col))
            url = _text(cell("Inputs", row, col + 1))
            quote = _text(cell("Inputs", row, col + 2))
            if label or url or quote:
                sources.append(Source(label, url, quote))
        specs.append(
            InputSpec(
                id=iid,
                row=row,
                group=_text(cell("Inputs", row, 2)),
                name=_text(cell("Inputs", row, 3)),
                unit=_text(cell("Inputs", row, 4)),
                low=float(cell("Inputs", row, 5)),
                base=float(cell("Inputs", row, 6)),
                high=float(cell("Inputs", row, 7)),
                case=case,
                confidence=_text(cell("Inputs", row, 10)),
                verdict=_text(cell("Inputs", row, 11)),
                rationale=_text(cell("Inputs", row, 12)),
                correction=_text(cell("Inputs", row, 13)),
                sources=tuple(sources),
            )
        )

    # --- Hazards: depth-damage curves -------------------------------------------------
    curves = {"depth": [], "africa": [], "global": []}
    for row in range(5, 11):
        curves["depth"].append(float(cell("Hazards", row, 1)))
        curves["africa"].append(float(cell("Hazards", row, 2)))
        curves["global"].append(float(cell("Hazards", row, 3)))

    # --- Portfolio include switches ---------------------------------------------------
    include, option_names = {}, {}
    for offset, oid in enumerate(OPTION_IDS):
        row = 5 + offset
        if _text(cell("Portfolio", row, 2)) != oid:
            raise ValueError(f"Portfolio!B{row}: expected option {oid}")
        include[oid] = bool(cell("Portfolio", row, 3))
        option_names[oid] = _text(cell("Portfolio", row, 1))

    # --- Display sheets ---------------------------------------------------------------
    def rows(sheet: str, first: int, ncols: int, stop_blank: bool = True):
        out = []
        for row in range(first, wb_f[sheet].max_row + 1):
            vals = [cell(sheet, row, col) for col in range(1, ncols + 1)]
            if vals[0] is None:
                if stop_blank:
                    break
                continue
            out.append(vals)
        return out

    gates = tuple(
        {"option": _text(r[0]), "gate": _text(r[1]), "inputs": _text(r[2]), "status": _text(r[3])}
        for r in rows("Gates", 4, 4)
    )
    audit_problems = tuple(
        {"source": _text(r[0]), "problem": _text(r[1]), "affects": _text(r[2])}
        for r in rows("Source audit", 4, 3)
    )
    spot_first = next(
        r for r in range(1, wb_f["Source audit"].max_row + 1) if _text(cell("Source audit", r, 1)) == "Claim"
    )
    audit_spotcheck = tuple(
        {"claim": _text(r[0]), "text": _text(r[1]), "verdict": _text(r[2]), "url": _text(r[3])}
        for r in rows("Source audit", spot_first + 1, 4)
    )
    sources = tuple(
        {"source": _text(r[0]), "url": _text(r[1]), "used_by": _text(r[2])} for r in rows("Sources", 4, 3)
    )
    sens_scenarios = tuple(
        {"option": _text(r[0]), "benign": float(r[1]), "base": float(r[2]), "stress": float(r[3])}
        for r in rows("Sensitivity", 6, 4)
    )
    tornado_first = next(
        r for r in range(1, wb_f["Sensitivity"].max_row + 1) if _text(cell("Sensitivity", r, 1)) == "Input"
    )
    sens_tornado = tuple(
        {
            "id": _text(r[0]).split(" ")[0],
            "label": _text(r[0]),
            "low": float(r[1]),
            "high": float(r[2]),
            "swing": float(r[3]),
            "base": float(r[4]),
            "reading": _text(r[6]),
        }
        for r in rows("Sensitivity", tornado_first + 1, 7)
    )
    readme = tuple((_text(r[0]), _text(r[1])) for r in rows("README", 3, 2))

    return WorkbookData(
        path=str(path),
        inputs=tuple(specs),
        curves=curves,
        include=include,
        option_names=option_names,
        gates=gates,
        audit_problems=audit_problems,
        audit_spotcheck=audit_spotcheck,
        sources=sources,
        sens_scenarios=sens_scenarios,
        sens_tornado=sens_tornado,
        readme=readme,
    )


@dataclass
class InputState:
    """Editable copy of the register: Low/Base/High, the case per input, and custom values.

    The active value of an input is, in order:
      1. its custom value, if one is set (the app's sliders write here);
      2. otherwise Low, Base or High by its case, as in Inputs column I. Rows with case "S"
         take their case from the scenario switch G09, as in Inputs column H.
    """

    low: dict
    base: dict
    high: dict
    case: dict
    custom: dict = field(default_factory=dict)

    @classmethod
    def from_specs(cls, specs) -> "InputState":
        return cls(
            low={s.id: s.low for s in specs},
            base={s.id: s.base for s in specs},
            high={s.id: s.high for s in specs},
            case={s.id: s.case for s in specs},
        )

    def copy(self) -> "InputState":
        return InputState(dict(self.low), dict(self.base), dict(self.high), dict(self.case), dict(self.custom))

    def pick(self, iid: str, case: str) -> float:
        """=IF(H="L", Low, IF(H="H", High, Base))"""
        if case == "L":
            return self.low[iid]
        if case == "H":
            return self.high[iid]
        return self.base[iid]

    def scenario(self, overrides: dict | None = None) -> int:
        """Active G09, truncated and clamped to 1..3 like CHOOSE(in_G09, ...)."""
        if overrides and SCENARIO_ID in overrides:
            value = overrides[SCENARIO_ID]
        elif SCENARIO_ID in self.custom:
            value = self.custom[SCENARIO_ID]
        else:
            value = self.pick(SCENARIO_ID, self.case[SCENARIO_ID])
        return min(3, max(1, int(value)))

    def resolved_case(self, iid: str, scenario: int | None = None) -> str:
        case = self.case[iid]
        if case == SCENARIO_CASE:
            return "LBH"[(scenario or self.scenario()) - 1]
        return case

    def values(self, overrides: dict | None = None) -> dict:
        """Active value of every input. `overrides` act like extra custom values for one run."""
        scenario = self.scenario(overrides)
        scenario_case = "LBH"[scenario - 1]
        low, base, high, custom = self.low, self.base, self.high, self.custom
        out = {}
        for iid, case in self.case.items():
            if iid in custom:
                out[iid] = custom[iid]
                continue
            if case == SCENARIO_CASE:
                case = scenario_case
            out[iid] = low[iid] if case == "L" else high[iid] if case == "H" else base[iid]
        if overrides:
            out.update(overrides)
        return out

    def signature(self) -> tuple:
        """Hashable identity of the whole state, for caching results that depend on it."""
        return (tuple(self.low.values()), tuple(self.base.values()), tuple(self.high.values()),
                tuple(self.case.values()), tuple(sorted(self.custom.items())))

    def moved(self, specs) -> list:
        """Inputs whose active value differs from the workbook's base case: (id, workbook, now)."""
        ref = InputState.from_specs(specs).values()
        now = self.values()
        return [(s.id, ref[s.id], now[s.id]) for s in specs if abs(now[s.id] - ref[s.id]) > 1e-12]

    # --- CSV round trip ---------------------------------------------------------------
    def to_csv(self, specs) -> str:
        now = self.values()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_FIELDS)
        for s in specs:
            writer.writerow(
                [
                    s.id,
                    s.name,
                    s.unit,
                    repr(float(self.low[s.id])),
                    repr(float(self.base[s.id])),
                    repr(float(self.high[s.id])),
                    self.case[s.id],
                    repr(float(self.custom[s.id])) if s.id in self.custom else "",
                    repr(float(now[s.id])),
                ]
            )
        return buf.getvalue()

    @classmethod
    def from_csv(cls, text: str, specs) -> tuple["InputState", list]:
        """Restore a state from a CSV written by `to_csv`. Returns (state, problems)."""
        state = cls.from_specs(specs)
        by_id = {s.id: s for s in specs}
        problems = []
        reader = csv.DictReader(io.StringIO(text))
        missing = [f for f in ("ID", "Low", "Base", "High", "Case") if f not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(missing)}")
        seen = set()
        for line, rec in enumerate(reader, start=2):
            iid = (rec.get("ID") or "").strip()
            if iid not in by_id:
                problems.append(f"line {line}: unknown input ID {iid!r}, skipped")
                continue
            seen.add(iid)
            try:
                low, base, high = (float(rec[k]) for k in ("Low", "Base", "High"))
            except (TypeError, ValueError):
                problems.append(f"line {line}: {iid} has a non-numeric Low/Base/High, kept workbook values")
                continue
            case = (rec.get("Case") or "").strip().upper()
            if case not in ("L", "B", "H", SCENARIO_CASE) or (case == SCENARIO_CASE and not by_id[iid].follows_scenario):
                problems.append(f"line {line}: {iid} has case {case!r}, kept {state.case[iid]!r}")
                case = state.case[iid]
            state.low[iid], state.base[iid], state.high[iid], state.case[iid] = low, base, high, case
            custom = (rec.get("Custom") or "").strip()
            if custom:
                try:
                    state.custom[iid] = float(custom)
                except ValueError:
                    problems.append(f"line {line}: {iid} has a non-numeric custom value, ignored")
        absent = [s.id for s in specs if s.id not in seen]
        if absent:
            problems.append(f"{len(absent)} inputs not in the file kept their workbook values: {', '.join(absent[:8])}"
                            + ("…" if len(absent) > 8 else ""))
        return state, problems


CASE_LABELS = {"L": "L", "B": "B", "H": "H", SCENARIO_CASE: "Scenario"}
_CASE_CODES = {label: code for code, label in CASE_LABELS.items()}


def apply_table_edits(state: InputState, by_id: dict, ids: list, edited_rows: dict, details: list) -> tuple[list, list]:
    """Apply edits from the app's input table to the state.

    `edited_rows` maps a row position in `ids` to {column: new value}, with numbers in display
    units (percentages as %). Columns: Low, Base, High, Case, Custom (None clears it) and
    Sources (show or hide the detail panel). Returns (details, problems).
    """
    details = list(details)
    problems = []
    for position, changes in edited_rows.items():
        iid = ids[int(position)]
        spec = by_id[iid]
        for column, value in changes.items():
            if column == "Sources":
                details = [d for d in details if d != iid] + ([iid] if value else [])
            elif column in ("Low", "Base", "High"):
                if value is None:
                    problems.append(f"{iid}: {column} cannot be empty; kept the previous value.")
                else:
                    getattr(state, column.lower())[iid] = float(value) / spec.display_scale
            elif column == "Case":
                code = _CASE_CODES.get(value)
                if code is None or (code == SCENARIO_CASE and not spec.follows_scenario):
                    problems.append(f"{iid} does not follow the hazard scenario; its case stays "
                                    f"{CASE_LABELS[state.case[iid]]}.")
                else:
                    state.case[iid] = code
            elif column == "Custom":
                if value is None:
                    state.custom.pop(iid, None)
                else:
                    state.custom[iid] = float(value) / spec.display_scale
    return details, problems
