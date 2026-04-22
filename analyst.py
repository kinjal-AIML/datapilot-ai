"""Analyst engine for DataPilot AI.

Two backends implement the same ``AnalystResponse`` contract:

* ``MockAnalyst`` — deterministic pandas-based answers for common business
  questions. Runs without any network or API key.
* ``LLMAnalyst``  — wraps the system prompt in ``SYSTEM_PROMPT`` and calls
  OpenAI. Only used when ``OPENAI_API_KEY`` is set.

The Flask app picks the right backend at request time.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd


SYSTEM_PROMPT = """You are a senior data analyst and BI engine inside a production system called \"DataPilot AI\".

The system allows users to upload Excel/CSV files containing sales and purchase data, and ask business questions.

You will receive:
1. TABLE SCHEMA (cleaned column names)
2. SAMPLE DATA (first few rows from each table)
3. USER QUESTION

YOUR RESPONSIBILITIES:
1. Understand the dataset structure
2. Generate a correct and executable query (SQL or pandas-style)
3. Identify metrics and dimensions
4. Prepare structured output for dashboard rendering

BUSINESS DEFINITIONS:
- Revenue = SUM(sales.revenue)
- Cost    = SUM(purchase.cost)
- Profit  = Revenue - Cost
- Orders  = COUNT(sales.id)

OUTPUT FORMAT (STRICT JSON ONLY, NO EXTRA TEXT):
{
  "query_type": "sql | pandas",
  "query": "generated query",
  "intent": "trend | comparison | distribution | summary",
  "dimensions": ["column1"],
  "metrics": ["metric1"],
  "kpis": {
    "total_revenue": number or null,
    "total_profit":  number or null,
    "total_orders":  number or null
  },
  "chart_hint": "line | bar | pie | table",
  "data": [ { "column": "value" } ],
  "insights": ["data-driven observation"],
  "explanation": "short business explanation",
  "error": null
}

RULES:
1. Use ONLY columns from schema. Do NOT invent columns. Missing column -> return error.
2. Prefer SQL if relational structure exists; use proper JOIN on product (or a relevant key);
   always aggregate (SUM/COUNT) when needed.
3. Time questions -> group by appropriate granularity (day/month/year) and sort chronologically.
4. Fill KPI values only when clearly computable; otherwise null.
5. Insights must be factual and data-driven; no generic statements.
6. chart_hint: line for time trends, bar for category comparison, pie for <=5-way distribution,
   table as fallback.
7. On any unanswerable question return the error envelope with error=\"Insufficient or invalid data\".
8. Output ONLY valid JSON. No markdown, no prose, no comments.
"""


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


@dataclass
class AnalystResponse:
    query_type: str = ""
    query: str = ""
    intent: str = ""
    dimensions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    kpis: dict[str, float | None] = field(
        default_factory=lambda: {
            "total_revenue": None,
            "total_profit": None,
            "total_orders": None,
        }
    )
    chart_hint: str = "table"
    data: list[dict[str, Any]] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    explanation: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def error_response(message: str) -> "AnalystResponse":
        resp = AnalystResponse()
        resp.chart_hint = "table"
        resp.error = message
        return resp


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


# Candidate column names we accept for each logical field. Matched after
# ``clean_column`` has already normalised headers to snake_case.
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "date": ("date", "order_date", "invoice_date", "txn_date"),
    "product": ("item_name", "product", "product_name", "sku", "item"),
    "category": ("product_category", "item_cat", "category"),
    "quantity": ("quantity", "qty"),
    "revenue": ("value", "revenue", "gross_total", "amount", "sales_value"),
    "cost": ("value", "cost", "purchase_value", "amount", "gross_total"),
    "id": ("voucher_no", "order_no", "invoice_no", "id"),
    "customer": ("customer", "customer_name", "client", "buyer"),
    "vendor": ("vendor", "supplier", "vendor_name"),
    "country": ("country_to", "country", "destination_country"),
    "rate": ("rate", "price", "unit_price"),
}


def resolve_column(df: pd.DataFrame, logical: str) -> str | None:
    """Return the first matching column in ``df`` for a logical field."""

    cols = list(df.columns)
    for cand in _CANDIDATES.get(logical, ()):  # exact match first
        if cand in cols:
            return cand
    # Fuzzy: any column containing the logical keyword.
    for col in cols:
        if logical in col:
            return col
    return None


# ---------------------------------------------------------------------------
# Mock analyst
# ---------------------------------------------------------------------------


class MockAnalyst:
    """Deterministic pandas-backed analyst for the common question shapes.

    Covered question shapes:

    * overall summary / KPIs ("summary", "overview", "kpis")
    * revenue / cost / profit over time ("monthly", "trend", "over time")
    * top N by some dimension ("top products", "best customers", ...)
    * breakdown by a dimension ("by country", "by category", ...)

    Anything we can't confidently map falls through to a schema summary so the
    user at least sees something sensible instead of a hard error.
    """

    def __init__(self, sales: pd.DataFrame | None, purchase: pd.DataFrame | None):
        self.sales = sales
        self.purchase = purchase

    # --- public ----------------------------------------------------------

    def answer(self, question: str) -> AnalystResponse:
        q = (question or "").lower().strip()
        if not q:
            return AnalystResponse.error_response("Empty question")
        if self.sales is None and self.purchase is None:
            return AnalystResponse.error_response(
                "No data uploaded. Upload sales and/or purchase files first."
            )

        intent = self._detect_intent(q)
        try:
            if intent == "trend":
                return self._trend(q)
            if intent == "topn":
                return self._top_n(q)
            if intent == "breakdown":
                return self._breakdown(q)
            return self._summary(q)
        except _AnalystError as exc:
            return AnalystResponse.error_response(str(exc))

    # --- intent detection -----------------------------------------------

    @staticmethod
    def _detect_intent(q: str) -> str:
        if any(w in q for w in ("trend", "over time", "monthly", "per month",
                                "by month", "weekly", "daily", "yearly",
                                "per year", "by year", "time series")):
            return "trend"
        if re.search(r"\btop\s*\d+\b", q) or "top " in q or "best " in q:
            return "topn"
        if " by " in q or "breakdown" in q or "distribution" in q or "split" in q:
            return "breakdown"
        return "summary"

    # --- summary --------------------------------------------------------

    def _summary(self, q: str) -> AnalystResponse:
        kpis = self._kpis()
        rows: list[dict[str, Any]] = []
        for name, value in kpis.items():
            rows.append({"metric": name, "value": _maybe_round(value)})
        resp = AnalystResponse(
            query_type="pandas",
            query=self._summary_query(),
            intent="summary",
            dimensions=[],
            metrics=["total_revenue", "total_cost", "total_profit", "total_orders"],
            kpis={
                "total_revenue": _maybe_round(kpis["total_revenue"]),
                "total_profit": _maybe_round(kpis["total_profit"]),
                "total_orders": _maybe_round(kpis["total_orders"]),
            },
            chart_hint="bar",
            data=rows,
            insights=self._summary_insights(kpis),
            explanation=(
                "Aggregated KPIs across the full uploaded dataset: revenue from "
                "sales, cost from purchases, profit as the difference, and order "
                "count from unique sales vouchers."
            ),
        )
        return resp

    def _kpis(self) -> dict[str, float | None]:
        revenue = self._sum(self.sales, "revenue")
        cost = self._sum(self.purchase, "cost")
        profit = None
        if revenue is not None and cost is not None:
            profit = revenue - cost
        elif revenue is not None and cost is None:
            profit = None
        orders = self._orders()
        return {
            "total_revenue": revenue,
            "total_cost": cost,
            "total_profit": profit,
            "total_orders": orders,
        }

    def _summary_insights(self, kpis: dict[str, float | None]) -> list[str]:
        insights: list[str] = []
        rev, cost, profit, orders = (
            kpis["total_revenue"],
            kpis["total_cost"],
            kpis["total_profit"],
            kpis["total_orders"],
        )
        if rev is not None:
            insights.append(f"Total revenue is {_fmt_money(rev)} across all sales rows.")
        if cost is not None:
            insights.append(f"Total cost is {_fmt_money(cost)} across all purchase rows.")
        if profit is not None and rev:
            margin = (profit / rev) * 100 if rev else 0
            insights.append(
                f"Profit is {_fmt_money(profit)} — a gross margin of {margin:.1f}%."
            )
        if orders is not None:
            insights.append(f"There are {int(orders):,} sales orders in the dataset.")
        return insights or ["No KPIs could be computed from the uploaded data."]

    # --- trend ----------------------------------------------------------

    def _trend(self, q: str) -> AnalystResponse:
        gran = "ME"
        gran_label = "month"
        if "year" in q:
            gran, gran_label = "YE", "year"
        elif "week" in q:
            gran, gran_label = "W", "week"
        elif "day" in q or "daily" in q:
            gran, gran_label = "D", "day"

        want_profit = "profit" in q
        want_cost = "cost" in q and not want_profit
        want_revenue = not want_cost and not want_profit or "revenue" in q

        sales_series = self._time_sum(self.sales, "revenue", gran) if self.sales is not None else None
        purch_series = self._time_sum(self.purchase, "cost", gran) if self.purchase is not None else None

        # Build a union index so we can align sales/purchase for profit.
        idx: pd.DatetimeIndex
        if sales_series is not None and purch_series is not None:
            idx = sales_series.index.union(purch_series.index)
        elif sales_series is not None:
            idx = sales_series.index
        elif purch_series is not None:
            idx = purch_series.index
        else:
            raise _AnalystError("No date column found in the uploaded data.")

        rows: list[dict[str, Any]] = []
        metrics: list[str] = []
        for period in idx:
            row: dict[str, Any] = {"period": _fmt_period(period, gran)}
            if want_revenue:
                val = float(sales_series.get(period, 0.0)) if sales_series is not None else 0.0
                row["revenue"] = _maybe_round(val)
                if "revenue" not in metrics:
                    metrics.append("revenue")
            if want_cost:
                val = float(purch_series.get(period, 0.0)) if purch_series is not None else 0.0
                row["cost"] = _maybe_round(val)
                if "cost" not in metrics:
                    metrics.append("cost")
            if want_profit:
                rev = float(sales_series.get(period, 0.0)) if sales_series is not None else 0.0
                cost = float(purch_series.get(period, 0.0)) if purch_series is not None else 0.0
                row["profit"] = _maybe_round(rev - cost)
                if "profit" not in metrics:
                    metrics.append("profit")
            rows.append(row)

        kpis = self._kpis()
        resp = AnalystResponse(
            query_type="pandas",
            query=self._trend_query(gran_label, metrics),
            intent="trend",
            dimensions=["period"],
            metrics=metrics,
            kpis={
                "total_revenue": _maybe_round(kpis["total_revenue"]),
                "total_profit": _maybe_round(kpis["total_profit"]),
                "total_orders": _maybe_round(kpis["total_orders"]),
            },
            chart_hint="line",
            data=rows,
            insights=self._trend_insights(rows, metrics, gran_label),
            explanation=(
                f"Aggregated {', '.join(metrics) or 'revenue'} by {gran_label} and "
                "sorted chronologically. Profit per period is revenue minus matched "
                "purchase cost for that period."
            ),
        )
        return resp

    def _trend_insights(self, rows: list[dict[str, Any]], metrics: list[str], gran: str) -> list[str]:
        if len(rows) < 2 or not metrics:
            return []
        insights: list[str] = []
        for metric in metrics:
            values = [(r["period"], r.get(metric, 0) or 0) for r in rows]
            # Peak period.
            peak = max(values, key=lambda x: x[1])
            trough = min(values, key=lambda x: x[1])
            insights.append(
                f"{metric.title()} peaked in {peak[0]} at {_fmt_money(peak[1])} and "
                f"was lowest in {trough[0]} at {_fmt_money(trough[1])}."
            )
            if len(values) >= 2 and values[0][1]:
                first, last = values[0][1], values[-1][1]
                change = ((last - first) / first) * 100
                direction = "up" if change >= 0 else "down"
                insights.append(
                    f"{metric.title()} is {direction} {abs(change):.1f}% from "
                    f"{values[0][0]} to {values[-1][0]}."
                )
        return insights

    # --- top N ----------------------------------------------------------

    def _top_n(self, q: str) -> AnalystResponse:
        m = re.search(r"top\s*(\d+)", q)
        n = int(m.group(1)) if m else 5

        if "customer" in q:
            dim_logical, source = "customer", self.sales
            measure, measure_logical = "revenue", "revenue"
        elif "vendor" in q or "supplier" in q:
            dim_logical, source = "vendor", self.purchase
            measure, measure_logical = "cost", "cost"
        elif "country" in q or "destination" in q:
            dim_logical = "country"
            if "cost" in q and self.purchase is not None:
                source, measure, measure_logical = self.purchase, "cost", "cost"
            else:
                source, measure, measure_logical = self.sales, "revenue", "revenue"
        elif "category" in q:
            dim_logical = "category"
            if "cost" in q and self.purchase is not None:
                source, measure, measure_logical = self.purchase, "cost", "cost"
            else:
                source, measure, measure_logical = self.sales, "revenue", "revenue"
        else:  # default to products
            dim_logical = "product"
            if "cost" in q and self.purchase is not None:
                source, measure, measure_logical = self.purchase, "cost", "cost"
            else:
                source, measure, measure_logical = self.sales, "revenue", "revenue"

        if source is None:
            raise _AnalystError(
                f"The required table for this question is not uploaded "
                f"(need {'purchase' if measure == 'cost' else 'sales'} data)."
            )
        dim_col = resolve_column(source, dim_logical)
        mes_col = resolve_column(source, measure_logical)
        if dim_col is None or mes_col is None:
            raise _AnalystError(
                f"Required column missing: need '{dim_logical}' and '{measure_logical}'."
            )

        agg = (
            source.groupby(dim_col, dropna=True)[mes_col]
            .sum()
            .sort_values(ascending=False)
            .head(n)
        )
        rows = [
            {dim_logical: str(k), measure: _maybe_round(float(v))}
            for k, v in agg.items()
        ]
        total = float(source[mes_col].sum()) if len(source) else 0.0
        share = agg.sum() / total * 100 if total else 0

        insights: list[str] = []
        if rows:
            insights.append(
                f"Top {dim_logical} by {measure} is '{rows[0][dim_logical]}' at "
                f"{_fmt_money(rows[0][measure])}."
            )
            insights.append(
                f"The top {len(rows)} {dim_logical}s account for "
                f"{share:.1f}% of total {measure}."
            )
        kpis = self._kpis()
        return AnalystResponse(
            query_type="pandas",
            query=f"{_source_name(source, self)}.groupby('{dim_col}')['{mes_col}']"
            f".sum().sort_values(ascending=False).head({n})",
            intent="comparison",
            dimensions=[dim_logical],
            metrics=[measure],
            kpis={
                "total_revenue": _maybe_round(kpis["total_revenue"]),
                "total_profit": _maybe_round(kpis["total_profit"]),
                "total_orders": _maybe_round(kpis["total_orders"]),
            },
            chart_hint="bar",
            data=rows,
            insights=insights,
            explanation=(
                f"Grouped by {dim_logical}, summed {measure}, and returned the top {n}."
            ),
        )

    # --- breakdown ------------------------------------------------------

    def _breakdown(self, q: str) -> AnalystResponse:
        # "by X" extraction.
        m = re.search(r"by\s+(\w[\w\s]*)", q)
        if m:
            dim_key = m.group(1).strip().split()[0]
        else:
            dim_key = "country"

        # Map user words to our logical columns.
        alias = {
            "country": "country",
            "destination": "country",
            "customer": "customer",
            "client": "customer",
            "vendor": "vendor",
            "supplier": "vendor",
            "category": "category",
            "product": "product",
            "item": "product",
        }
        dim_logical = alias.get(dim_key, dim_key)

        if "cost" in q and self.purchase is not None:
            source, measure, measure_logical = self.purchase, "cost", "cost"
        else:
            source, measure, measure_logical = self.sales, "revenue", "revenue"
        if source is None:
            raise _AnalystError("Required table not uploaded for this breakdown.")

        dim_col = resolve_column(source, dim_logical)
        mes_col = resolve_column(source, measure_logical)
        if dim_col is None or mes_col is None:
            raise _AnalystError(
                f"Required column missing for breakdown by {dim_logical}."
            )
        agg = source.groupby(dim_col, dropna=True)[mes_col].sum().sort_values(ascending=False)
        rows = [
            {dim_logical: str(k), measure: _maybe_round(float(v))}
            for k, v in agg.items()
        ]
        chart_hint = "pie" if 0 < len(rows) <= 5 else "bar"
        insights: list[str] = []
        if rows:
            total = sum(r[measure] for r in rows) or 1
            top = rows[0]
            insights.append(
                f"'{top[dim_logical]}' leads with {_fmt_money(top[measure])} "
                f"({top[measure] / total * 100:.1f}% of total)."
            )
            insights.append(f"{len(rows)} distinct {dim_logical} values contribute to {measure}.")
        kpis = self._kpis()
        return AnalystResponse(
            query_type="pandas",
            query=f"{_source_name(source, self)}.groupby('{dim_col}')['{mes_col}'].sum()",
            intent="distribution",
            dimensions=[dim_logical],
            metrics=[measure],
            kpis={
                "total_revenue": _maybe_round(kpis["total_revenue"]),
                "total_profit": _maybe_round(kpis["total_profit"]),
                "total_orders": _maybe_round(kpis["total_orders"]),
            },
            chart_hint=chart_hint,
            data=rows,
            insights=insights,
            explanation=f"{measure.title()} grouped by {dim_logical}, sorted descending.",
        )

    # --- helpers --------------------------------------------------------

    def _sum(self, df: pd.DataFrame | None, logical: str) -> float | None:
        if df is None:
            return None
        col = resolve_column(df, logical)
        if col is None:
            return None
        try:
            return float(pd.to_numeric(df[col], errors="coerce").sum())
        except Exception:
            return None

    def _orders(self) -> float | None:
        if self.sales is None:
            return None
        id_col = resolve_column(self.sales, "id")
        if id_col is None:
            return float(len(self.sales))
        return float(self.sales[id_col].nunique())

    def _time_sum(self, df: pd.DataFrame, measure_logical: str, gran: str) -> pd.Series | None:
        date_col = resolve_column(df, "date")
        mes_col = resolve_column(df, measure_logical)
        if not date_col or not mes_col:
            return None
        series = df.assign(_d=pd.to_datetime(df[date_col], errors="coerce"))
        series = series.dropna(subset=["_d"])
        if series.empty:
            return None
        series["_v"] = pd.to_numeric(series[mes_col], errors="coerce").fillna(0)
        grouped = series.groupby(pd.Grouper(key="_d", freq=gran))["_v"].sum().sort_index()
        return grouped

    def _summary_query(self) -> str:
        return (
            "SELECT SUM(sales.value) AS total_revenue, "
            "SUM(purchase.value) AS total_cost, "
            "SUM(sales.value) - SUM(purchase.value) AS total_profit, "
            "COUNT(DISTINCT sales.voucher_no) AS total_orders "
            "FROM sales CROSS JOIN purchase"
        )

    def _trend_query(self, gran_label: str, metrics: list[str]) -> str:
        gran = {"year": "year", "month": "month", "week": "week", "day": "day"}.get(
            gran_label, "month"
        )
        return _trend_sql(gran, metrics)


def _trend_sql(gran: str, metrics: list[str]) -> str:
    m_sql: list[str] = []
    if "revenue" in metrics:
        m_sql.append("SUM(sales.value) AS revenue")
    if "cost" in metrics:
        m_sql.append("SUM(purchase.value) AS cost")
    if "profit" in metrics:
        m_sql.append("SUM(sales.value) - SUM(purchase.value) AS profit")
    select = ", ".join(m_sql) or "SUM(sales.value) AS revenue"
    return (
        f"SELECT DATE_TRUNC('{gran}', date) AS period, {select} "
        "FROM sales FULL OUTER JOIN purchase USING (product, date) "
        "GROUP BY period ORDER BY period"
    )


class _AnalystError(RuntimeError):
    """Raised internally when a mock analyst question cannot be answered."""


def _source_name(df: pd.DataFrame, analyst: MockAnalyst) -> str:
    if analyst.sales is df:
        return "sales"
    if analyst.purchase is df:
        return "purchase"
    return "df"


def _fmt_money(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    absv = abs(v)
    if absv >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if absv >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if absv >= 1_000:
        return f"{v / 1_000:.2f}K"
    return f"{v:,.2f}"


def _fmt_period(period: pd.Timestamp, gran: str) -> str:
    g = gran.upper()
    if g.startswith("Y"):
        return period.strftime("%Y")
    if g.startswith("M"):
        return period.strftime("%Y-%m")
    if g.startswith("W"):
        return period.strftime("%Y-W%U")
    return period.strftime("%Y-%m-%d")


def _maybe_round(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int,)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 2)
    return value


# ---------------------------------------------------------------------------
# LLM analyst (optional)
# ---------------------------------------------------------------------------


class LLMAnalyst:
    """Calls OpenAI with the system prompt and returns parsed JSON.

    Falls back to the mock analyst if the model response is not valid JSON or
    is missing required fields. This keeps the UI contract stable even if the
    model misbehaves.
    """

    def __init__(self, api_key: str, model: str, fallback: MockAnalyst):
        from openai import OpenAI  # lazy import so tests don't need it

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.fallback = fallback

    def answer(self, question: str) -> AnalystResponse:
        context = self._context_payload(question)
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = completion.choices[0].message.content or ""
            parsed = json.loads(raw)
            return _coerce_response(parsed)
        except Exception:
            # Any network / parse / validation error -> deterministic fallback.
            return self.fallback.answer(question)

    def _context_payload(self, question: str) -> str:
        payload: dict[str, Any] = {"question": question, "tables": {}}
        for name, df in (("sales", self.fallback.sales), ("purchase", self.fallback.purchase)):
            if df is None:
                continue
            payload["tables"][name] = {
                "columns": list(df.columns),
                "dtypes": {c: str(df[c].dtype) for c in df.columns},
                "sample": df.head(3).astype(str).to_dict(orient="records"),
                "row_count": int(len(df)),
            }
        return json.dumps(payload, default=str)


def _coerce_response(parsed: dict[str, Any]) -> AnalystResponse:
    resp = AnalystResponse()
    resp.query_type = str(parsed.get("query_type", ""))
    resp.query = str(parsed.get("query", ""))
    resp.intent = str(parsed.get("intent", ""))
    dims = parsed.get("dimensions") or []
    resp.dimensions = [str(x) for x in dims if x is not None]
    mets = parsed.get("metrics") or []
    resp.metrics = [str(x) for x in mets if x is not None]
    kpis = parsed.get("kpis") or {}
    for key in ("total_revenue", "total_profit", "total_orders"):
        resp.kpis[key] = _maybe_round(kpis.get(key))
    resp.chart_hint = str(parsed.get("chart_hint") or "table")
    data = parsed.get("data") or []
    resp.data = [d for d in data if isinstance(d, dict)]
    insights = parsed.get("insights") or []
    resp.insights = [str(x) for x in insights]
    resp.explanation = str(parsed.get("explanation") or "")
    err = parsed.get("error")
    resp.error = None if err in (None, "", "null") else str(err)
    return resp


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_analyst(
    sales: pd.DataFrame | None, purchase: pd.DataFrame | None
):
    mock = MockAnalyst(sales, purchase)
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return mock
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    try:
        return LLMAnalyst(api_key=key, model=model, fallback=mock)
    except Exception:
        return mock
