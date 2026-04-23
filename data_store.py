"""In-memory session store for uploaded sales / purchase dataframes.
 
This is deliberately simple: a single-process, dict-backed store keyed by a
client-supplied session id. Good enough for a demo / local BI app; swap for
Redis or a real DB if this ever becomes multi-process.
"""
 
from __future__ import annotations
 
import io
import re
import threading
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any
 
import pandas as pd
 
 
_SAFE_RE = re.compile(r"[^a-z0-9]+")
 
 
def clean_column(name: str) -> str:
    """Normalise a column header to snake_case ASCII.
 
    ``"Item Name"`` -> ``"item_name"``, ``"Gross Total"`` -> ``"gross_total"``.
    """
 
    cleaned = _SAFE_RE.sub("_", str(name).strip().lower()).strip("_")
    return cleaned or "column"
 
 
def _dedupe(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for col in columns:
        if col in seen:
            seen[col] += 1
            out.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            out.append(col)
    return out
 
 
def read_tabular(filename: str, raw: bytes) -> pd.DataFrame:
    """Read an uploaded CSV or Excel file into a DataFrame.
 
    For Excel workbooks we pick the sheet with the most rows, which in the
    wild is almost always the real data sheet (pivot / summary sheets are
    smaller).
    """
 
    lower = filename.lower()
    if lower.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw))
    elif lower.endswith((".xlsx", ".xls", ".xlsm")):
        xl = pd.ExcelFile(io.BytesIO(raw))
        best_sheet = xl.sheet_names[0]
        best_rows = -1
        for sheet in xl.sheet_names:
            try:
                probe = xl.parse(sheet, nrows=0)
                rows = xl.book[sheet].max_row if hasattr(xl, "book") else None
            except Exception:
                rows = None
            if rows is None:
                probe = xl.parse(sheet)
                rows = len(probe)
            if rows > best_rows:
                best_rows = rows
                best_sheet = sheet
        df = xl.parse(best_sheet)
    else:
        raise ValueError(f"Unsupported file type: {filename}")
 
    df.columns = _dedupe([clean_column(c) for c in df.columns])
    # Drop completely blank columns (common in pivoted Excel exports).
    df = df.dropna(axis=1, how="all")
    # Coerce date-looking columns to datetime where possible.
    for col in df.columns:
        if "date" in col:
            df[col] = _to_datetime_safe(df[col])
    return df


def _to_datetime_safe(series: pd.Series) -> pd.Series:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.to_datetime(series, errors="coerce")
 
 
@dataclass
class Session:
    session_id: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
 
    def set_table(self, kind: str, df: pd.DataFrame) -> None:
        self.tables[kind] = df
 
    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for kind, df in self.tables.items():
            out[kind] = {
                "row_count": int(len(df)),
                "columns": [
                    {"name": c, "dtype": str(df[c].dtype)} for c in df.columns
                ],
                "sample": _sample_rows(df, 3),
            }
        return out
 
 
def _sample_rows(df: pd.DataFrame, n: int) -> list[dict[str, Any]]:
    head = df.head(n).copy()
    for col in head.columns:
        if pd.api.types.is_datetime64_any_dtype(head[col]):
            head[col] = head[col].dt.strftime("%Y-%m-%d")
    return [
        {k: (None if pd.isna(v) else v) for k, v in row.items()}
        for row in head.to_dict(orient="records")
    ]
 
 
class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
 
    def create(self) -> Session:
        with self._lock:
            sid = uuid.uuid4().hex
            session = Session(session_id=sid)
            self._sessions[sid] = session
            return session
 
    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)
 
    def get_or_create(self, session_id: str | None) -> Session:
        if session_id:
            existing = self.get(session_id)
            if existing is not None:
                return existing
        return self.create()