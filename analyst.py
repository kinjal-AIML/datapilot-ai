
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
import warnings
from dataclasses import asdict, dataclass, field
from typing import Any
 
import pandas as pd
 
 
SYSTEM_PROMPT = """You are a production-grade AI data analyst inside a system called \"DataPilot AI\".

Users upload CSV/Excel files (sales and purchase data) and ask questions in natural language.

YOU WILL RECEIVE:
1. DATABASE SCHEMA (cleaned column names)
2. SAMPLE DATA (a few rows from each table)
3. USER QUESTION (may be informal or incomplete)
4. MATCHED VALUES (preprocessed entities mapped to actual dataset values)

YOUR TASK:
1. Understand the user's intent from natural language
2. Use MATCHED VALUES to ground the query in real data
3. Generate a correct SQL query
4. Return structured output for dashboard rendering

CRITICAL RULE: MATCHED VALUES (MANDATORY)
MATCHED VALUES is keyed by the ACTUAL COLUMN NAME in the uploaded tables. Do NOT
invent a column. Use the column name exactly as it appears as the key.

Shape:
  {
    "<actual_column>": {"sales": ["..."], "purchase": ["..."]},
    "year":  "2024",
    "month": 3
  }

Rules:
- For every "<actual_column>" key present, the SQL MUST filter that column with
  IN (...) using the matched list for the table being queried.
  e.g. matched = {"item_name": {"purchase": ["WHEAT FLOUR"]}}
       -> WHERE purchase.item_name IN ('WHEAT FLOUR')
- If "year" is present:   AND EXTRACT(YEAR FROM <date_col>) = <year>
- If "month" is present:  AND EXTRACT(MONTH FROM <date_col>) = <month>
- NEVER put a value into a column other than the one it was matched against.
- If NO matched list exists for an entity the user mentioned, fall back to
  WHERE LOWER(<likely_column>) LIKE '%keyword%' using a column that actually
  exists in the provided SCHEMA.

SCHEMA RULE:
The user may upload arbitrary Excel/CSV files — column names vary. Use ONLY
column names that appear in the SCHEMA section of the payload. Never guess or
reuse column names from prior conversations or examples.

DATE COLUMN SELECTION (CRITICAL):
Each table in the payload lists all date-like columns under "date_columns"
with their min/max ranges. A table may have several (e.g. date,
supplier_invoice_date, po_order_date, order_date, invoice_date). Pick the
column that best matches the USER'S INTENT, not just the one called "date":

  - "purchased"           -> supplier_invoice_date (if present), else po_order_date, else date
  - "ordered" / "PO"      -> po_order_date (if present), else order_date, else date
  - "sold" / "sales"      -> order_date (if present on sales), else invoice_date, else date
  - "invoiced" / "billed" -> invoice_date / supplier_invoice_date (whichever side)
  - generic / unspecified -> the column literally named "date"

Always choose a column that is actually listed in "date_columns" for that
table. If the chosen column's min/max range does not contain the user's
requested year/month, still generate the SQL correctly but mention the
available range in the explanation.

BUSINESS DEFINITIONS (map to whichever real column exists in the SCHEMA):
- Revenue = SUM(<sales money column, e.g. value / gross_total / amount>)
- Cost    = SUM(<purchase money column, e.g. value / gross_total / amount>)
- Profit  = Revenue - Cost
- Orders  = COUNT(DISTINCT <sales identifier, e.g. voucher_no / order_no / invoice_no>)
- Quantity = SUM(<quantity column, e.g. quantity / qty>)

Never reference a column like "sales.id" or "purchase.id" unless that exact
column name appears in the SCHEMA. Always pick the closest real column.

INTENT DETECTION:
- "how many", "count"        -> count
- "sales", "revenue"         -> revenue metric
- "profit"                   -> profit
- "trend", "over time"       -> time-based grouping
- "top", "best"              -> ranking
- "purchase"                 -> use purchase table
- otherwise                  -> summary

SQL RULES:
1. Use ONLY columns from schema. Missing column -> return the error envelope.
2. JOIN properly when combining tables.
3. Aggregate (SUM, COUNT, GROUP BY) where appropriate.
4. Alias computed fields with meaningful names.
5. Date filters: EXTRACT(YEAR FROM date) = 2024 and/or EXTRACT(MONTH FROM date) = 3.
   If MATCHED VALUES contains "year" or "month", use them in the WHERE clause.
   Tables are registered in DuckDB as `sales` and `purchase` — use those names.
6. Always return meaningful column names.

OUTPUT FORMAT (STRICT JSON ONLY, NO EXTRA TEXT, NO MARKDOWN):
{
  "sql": "SQL query",
  "query_type": "sql",
  "query": "same as sql (kept for UI compatibility)",
  "intent": "count | trend | comparison | distribution | summary",
  "filters": {
    "product": [],
    "date": "",
    "other": []
  },
  "dimensions": ["column1"],
  "metrics": ["metric1"],
  "chart_hint": "line | bar | pie | table",
  "kpis": {
    "total_revenue": number or null,
    "total_profit":  number or null,
    "total_orders":  number or null
  },
  "data": [ { "column": "value" } ],
  "insights": ["data-driven observation"],
  "explanation": "short factual explanation",
  "error": null
}

CHART HINT:
- trend -> line
- comparison -> bar
- distribution (<=5 categories) -> pie
- single value -> table

ERROR HANDLING:
If the question cannot be answered from the schema, return the envelope with
error = "Insufficient or invalid data" and empty/neutral values elsewhere.

STRICT:
- Output ONLY valid JSON. No markdown. No prose. No hallucinated columns.
- Do not guess values not present in schema or matched values.
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
    sql: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    matched_values: dict[str, Any] = field(default_factory=dict)
 
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
 
 
def _to_datetime_safe(series: pd.Series) -> pd.Series:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.to_datetime(series, errors="coerce")
 
 
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
            if intent == "count":
                return self._count(q)
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
        if any(w in q for w in ("how many", "count", "number of")):
            return "count"
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
 
    def _count(self, q: str) -> AnalystResponse:
        use_purchase = "purchase" in q or "purchased" in q or "cost" in q
        source = self.purchase if use_purchase else self.sales
        if source is None:
            raise _AnalystError(
                "Upload sales or purchase data before asking this question."
            )

        date_col = resolve_column(source, "date")
        product_col = resolve_column(source, "product")
        qty_col = resolve_column(source, "quantity")
        id_col = resolve_column(source, "id")

        year = self._extract_year(q)
        product_keyword = self._extract_product_term(q)

        df = source.copy()
        if date_col and year:
            df = df.assign(_d=_to_datetime_safe(df[date_col])).dropna(subset=["_d"])
            df = df[df["_d"].dt.year == int(year)]
        if product_col and product_keyword:
            keyword = product_keyword.lower()
            df = df[df[product_col].astype(str).str.lower().str.contains(keyword, regex=False)]

        if df.empty:
            raise _AnalystError("No matching rows found for the requested filters.")

        if qty_col and any(w in q for w in ("unit", "quantity", "items", "purchased")):
            total = float(pd.to_numeric(df[qty_col], errors="coerce").sum())
            metric = "quantity"
            query_measure = f"SUM({qty_col}) AS total_quantity"
        elif id_col:
            total = float(df[id_col].nunique())
            metric = "orders"
            query_measure = f"COUNT(DISTINCT {id_col}) AS total_orders"
        else:
            total = float(len(df))
            metric = "count"
            query_measure = "COUNT(*) AS total_count"

        table_name = "purchase" if source is self.purchase else "sales"
        conditions: list[str] = []
        if year and date_col:
            conditions.append(f"EXTRACT(YEAR FROM {date_col}) = {year}")
        if product_col and product_keyword:
            keyword = product_keyword.lower().replace("'", "''")
            conditions.append(f"LOWER({product_col}) LIKE '%{keyword}%'" )
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT {query_measure} FROM {table_name}{where}"

        rows = [{metric: _maybe_round(total)}]
        resp = AnalystResponse(
            query_type="pandas",
            query=query,
            intent="count",
            dimensions=[],
            metrics=[metric],
            kpis={
                "total_revenue": None,
                "total_profit": None,
                "total_orders": None,
            },
            chart_hint="bar",
            data=rows,
            insights=[
                f"Computed {metric} for {product_keyword or 'matching items'}"
                + (f" in {year}" if year else "") + "."
            ],
            explanation=(
                f"Aggregated {metric} from the {'purchase' if source is self.purchase else 'sales'} table"
                + (f" filtered by {product_keyword}" if product_keyword else "")
                + (f" and year {year}" if year else "") + "."
            ),
        )
        return resp

    @staticmethod
    def _extract_year(q: str) -> str | None:
        m = re.search(r"\b(20\d{2})\b", q)
        return m.group(1) if m else None

    @staticmethod
    def _extract_product_term(q: str) -> str | None:
        m = re.search(r"how many\s+(?:units\s+of\s+|items\s+of\s+)?(.+?)\s+(?:purchased|sold|were|did|orders|items)\b", q)
        if m:
            return m.group(1).strip()
        m = re.search(r"(?:purchased|sold)\s+(.+?)\s+(?:in|during|for|$)", q)
        if m:
            return m.group(1).strip()
        return None

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

        # Determine logical dimension and source table
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
            # Prefer purchase data for "purchased" phrasing, otherwise sales
            if ("purchas" in q or "bought" in q or "purchased" in q) and self.purchase is not None:
                source, measure, measure_logical = self.purchase, "count", "quantity"
            elif "cost" in q and self.purchase is not None:
                source, measure, measure_logical = self.purchase, "cost", "cost"
            else:
                source, measure, measure_logical = self.sales, "revenue", "revenue"

        if source is None:
            raise _AnalystError(
                f"The required table for this question is not uploaded "
                f"(need {'purchase' if measure in ('cost','quantity') else 'sales'} data)."
            )

        dim_col = resolve_column(source, dim_logical)
        # For repetitive/top-by-frequency queries we'll use a count metric
        mes_col = None if measure == "count" else resolve_column(source, measure_logical)
        if dim_col is None or (measure != "count" and mes_col is None):
            raise _AnalystError(
                f"Required column missing: need '{dim_logical}' and '{measure_logical if measure!='count' else 'date/quantity'}'."
            )

        repetitive = any(w in q for w in ("repetit", "repeat", "repeated", "repeatedly", "frequency", "frequently"))
        year_spec = self._extract_year(q)

        # If user asked for repetitive/top-by-frequency year-wise, compute counts per year
        if repetitive:
            date_col = resolve_column(source, "date")
            if date_col is None:
                raise _AnalystError("No date column found for year-wise repetitive analysis.")
            df = source.copy()
            df = df.assign(_d=_to_datetime_safe(df[date_col])).dropna(subset=["_d"]) 
            if df.empty:
                raise _AnalystError("No valid date rows found in the data.")
            df["_year"] = df["_d"].dt.year
            if year_spec:
                df = df[df["_year"] == int(year_spec)]
            grouped = df.groupby(["_year", dim_col], dropna=True).size().reset_index(name="purchases")
            rows: list[dict[str, Any]] = []
            years = sorted(grouped["_year"].unique())
            if not years:
                raise _AnalystError("No matching rows found for the requested filters.")
            for y in years:
                top = grouped[grouped["_year"] == y].sort_values("purchases", ascending=False).head(n)
                for _, r in top.iterrows():
                    rows.append({"year": int(r["_year"]), dim_logical: str(r[dim_col]), "purchases": int(r["purchases"])})

            insights: list[str] = []
            if rows:
                top0 = rows[0]
                insights.append(
                    f"Top {dim_logical} in {top0['year']} is '{top0[dim_logical]}' with {top0['purchases']} purchases."
                )
            kpis = self._kpis()
            return AnalystResponse(
                query_type="pandas",
                query=(
                    "SELECT year, product, COUNT(*) as purchases FROM "
                    f"{_source_name(source, self)} GROUP BY year, product ORDER BY year, purchases DESC"
                ),
                intent="comparison",
                dimensions=["year", dim_logical],
                metrics=["purchases"],
                kpis={
                    "total_revenue": _maybe_round(kpis["total_revenue"]),
                    "total_profit": _maybe_round(kpis["total_profit"]),
                    "total_orders": _maybe_round(kpis["total_orders"]),
                },
                chart_hint="bar",
                data=rows,
                insights=insights,
                explanation=(
                    f"For each year, counted purchase rows per {dim_logical} and returned the top {n} by purchase frequency."
                ),
            )

        # Fallback: original monetary/top-n behavior
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
        series = df.assign(_d=_to_datetime_safe(df[date_col]))
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
# Entity matcher (grounds user question in real dataset values)
# ---------------------------------------------------------------------------


_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "by", "with",
    "how", "many", "much", "what", "which", "who", "when", "where", "why",
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "show", "me", "give", "tell", "find", "list", "get", "and", "or", "but",
    "top", "best", "worst", "total", "sum", "count", "number", "trend",
    "over", "time", "per", "by", "breakdown", "distribution", "split",
    "sales", "sold", "revenue", "purchase", "purchased", "cost", "profit",
    "orders", "order", "item", "items", "product", "products", "customer",
    "vendor", "supplier", "country", "category", "this", "that", "from",
    "year", "month", "week", "day",
}


class EntityMatcher:
    """Extract candidate entities from the user question and ground them in
    actual dataset values.

    Returns a dict like::

        {"product": ["Drum Stick Fresh", "Drumsticks"], "year": "2024"}

    Only logical fields that matched something are included.
    """

    _LOGICAL_FIELDS = ("product", "customer", "vendor", "country", "category")

    _MONTHS = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }

    def __init__(self, sales: pd.DataFrame | None, purchase: pd.DataFrame | None):
        self.sales = sales
        self.purchase = purchase

    def match(self, question: str) -> dict[str, Any]:
        """Ground user terms in the actual uploaded data.

        Returns a dict keyed by the **real column name** each table uses (not
        a logical alias), so the LLM cannot pick the wrong column. Shape::

            {
              "item_name": {"sales": ["WHEAT FLOUR"], "purchase": ["WHEAT FLOUR"]},
              "year": "2024",
              "month": 3
            }

        This makes the system schema-agnostic: a different uploaded workbook
        whose product column is named ``sku`` or ``product_name`` produces a
        matched dict keyed by those actual names.
        """

        q = (question or "").strip()
        if not q:
            return {}
        result: dict[str, Any] = {}

        year = self._extract_year(q)
        if year:
            result["year"] = year
        month = self._extract_month(q)
        if month:
            result["month"] = month

        tokens = self._tokens(q)
        if not tokens:
            return result

        q_lower = q.lower()
        for logical in self._LOGICAL_FIELDS:
            for table_name, df in (("sales", self.sales), ("purchase", self.purchase)):
                if df is None:
                    continue
                col = resolve_column(df, logical)
                if col is None:
                    continue
                matches = self._match_values(df, col, q_lower, tokens)
                if not matches:
                    continue
                bucket = result.setdefault(col, {})
                bucket[table_name] = matches
        return result

    @staticmethod
    def _extract_year(q: str) -> str | None:
        m = re.search(r"\b(20\d{2}|19\d{2})\b", q)
        return m.group(1) if m else None

    @classmethod
    def _extract_month(cls, q: str) -> int | None:
        ql = q.lower()
        for name, num in cls._MONTHS.items():
            if re.search(rf"\b{name}\b", ql):
                return num
        return None

    @staticmethod
    def _tokens(q: str) -> list[str]:
        raw = re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", q.lower())
        return [t for t in raw if t not in _STOPWORDS and len(t) >= 3]

    @staticmethod
    def _match_values(
        df: pd.DataFrame, col: str, q_lower: str, tokens: list[str]
    ) -> list[str]:
        try:
            vals = df[col].dropna().astype(str).unique().tolist()
        except Exception:
            return []

        # Score each candidate: 3=exact, 2=phrase-in-question, 1=token hit.
        scored: list[tuple[int, str]] = []
        for v in vals:
            val = v.strip()
            if not val:
                continue
            low = val.lower()
            if low == q_lower.strip():
                scored.append((3, val))
            elif low in q_lower:
                scored.append((2, val))
            elif any(tok in low for tok in tokens) and any(
                low_tok in q_lower for low_tok in low.split() if len(low_tok) >= 3
            ):
                scored.append((1, val))
        if not scored:
            return []
        scored.sort(key=lambda x: (-x[0], len(x[1])))
        seen: set[str] = set()
        out: list[str] = []
        for _, val in scored:
            if val in seen:
                continue
            seen.add(val)
            out.append(val)
            if len(out) >= 20:
                break
        return out


# ---------------------------------------------------------------------------
# SQL executor (runs LLM-generated SQL against uploaded DataFrames via duckdb)
# ---------------------------------------------------------------------------


class SQLExecutor:
    """Execute SQL against the uploaded sales/purchase DataFrames.

    Uses DuckDB with the DataFrames registered as views named ``sales`` and
    ``purchase``. DuckDB supports ``EXTRACT(YEAR FROM ...)``, ``DATE_TRUNC``,
    ``FULL OUTER JOIN``, etc. — the same dialect the system prompt asks for.
    """

    def __init__(self, sales: pd.DataFrame | None, purchase: pd.DataFrame | None):
        self.sales = sales
        self.purchase = purchase

    def available(self) -> bool:
        return self.sales is not None or self.purchase is not None

    def execute(self, sql: str, limit: int = 500) -> list[dict[str, Any]]:
        if not sql or not sql.strip():
            return []
        try:
            import duckdb  # lazy import
        except Exception:
            return []
        con = duckdb.connect(database=":memory:")
        try:
            if self.sales is not None:
                con.register("sales", self.sales)
            if self.purchase is not None:
                con.register("purchase", self.purchase)
            result = con.execute(sql).fetch_df()
        finally:
            con.close()
        if len(result) > limit:
            result = result.head(limit)
        # Stringify datetimes for JSON friendliness.
        for col in result.columns:
            if pd.api.types.is_datetime64_any_dtype(result[col]):
                result[col] = result[col].dt.strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        for rec in result.to_dict(orient="records"):
            clean: dict[str, Any] = {}
            for k, v in rec.items():
                if v is None:
                    clean[k] = None
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    clean[k] = None
                elif isinstance(v, (int, str, bool)):
                    clean[k] = v
                elif isinstance(v, float):
                    clean[k] = round(v, 2)
                else:
                    clean[k] = str(v)
            rows.append(clean)
        return rows


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
        self.matcher = EntityMatcher(fallback.sales, fallback.purchase)
        self.executor = SQLExecutor(fallback.sales, fallback.purchase)

    def answer(self, question: str) -> AnalystResponse:
        matched = self.matcher.match(question)
        context = self._context_payload(question, matched)
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
            resp = _coerce_response(parsed)
            if not resp.matched_values and matched:
                resp.matched_values = matched

            # Execute the generated SQL against the actual uploaded data.
            if resp.sql and self.executor.available():
                try:
                    rows = self.executor.execute(resp.sql)
                    if self._is_empty_result(rows):
                        resp.data = []
                        note = self._no_data_note()
                        resp.insights = [note]
                        if not resp.explanation:
                            resp.explanation = note
                        resp.chart_hint = "table"
                    elif rows:
                        resp.data = rows
                        resp.error = None
                        resp = self._enrich_from_rows(resp)
                except Exception as exc:
                    # SQL failed — keep whatever LLM produced (explanation,
                    # insights) so user still gets a genuine response.
                    if not resp.data and not resp.insights:
                        resp.insights = [
                            f"Could not execute generated SQL: {exc}."
                        ]

            if self._needs_fallback(resp):
                fb = self.fallback.answer(question)
                fb.matched_values = matched
                if resp.sql and not fb.sql:
                    fb.sql = resp.sql
                return fb
            return resp
        except Exception:
            fb = self.fallback.answer(question)
            fb.matched_values = matched
            return fb

    @staticmethod
    def _is_empty_result(rows: list[dict[str, Any]]) -> bool:
        if not rows:
            return True
        # A single-row aggregate with all-None values means "no matching rows".
        if len(rows) == 1 and all(v is None for v in rows[0].values()):
            return True
        return False

    def _no_data_note(self) -> str:
        ranges: list[str] = []
        for name, df in (("sales", self.fallback.sales), ("purchase", self.fallback.purchase)):
            if df is None:
                continue
            date_col = resolve_column(df, "date")
            if not date_col:
                continue
            try:
                d = pd.to_datetime(df[date_col], errors="coerce").dropna()
                if d.empty:
                    continue
                ranges.append(
                    f"{name}: {d.min().strftime('%Y-%m-%d')} to {d.max().strftime('%Y-%m-%d')}"
                )
            except Exception:
                pass
        suffix = f" Available date range - {'; '.join(ranges)}." if ranges else ""
        return "No rows match the requested filters." + suffix

    @staticmethod
    def _enrich_from_rows(resp: AnalystResponse) -> AnalystResponse:
        """If KPIs are null, try to lift them from a single-row result set."""
        if not resp.data:
            return resp
        if len(resp.data) == 1:
            row = resp.data[0]
            for key in ("total_revenue", "total_profit", "total_orders"):
                if resp.kpis.get(key) in (None, 0) and key in row:
                    resp.kpis[key] = _maybe_round(row.get(key))
        return resp

    def _needs_fallback(self, resp: AnalystResponse) -> bool:
        if resp.error:
            return True
        if resp.data:
            return False
        if resp.sql or resp.query:
            # We have a query but no rows — either executor ran and got an
            # empty result (legit "no data") or no executor available.
            # Either way, don't mask the LLM's explanation with mock output.
            return False
        return True
 
    def _context_payload(self, question: str, matched: dict[str, Any]) -> str:
        payload: dict[str, Any] = {
            "question": question,
            "matched_values": matched,
            "tables": {},
        }
        for name, df in (("sales", self.fallback.sales), ("purchase", self.fallback.purchase)):
            if df is None:
                continue
            date_columns: list[dict[str, str]] = []
            for col in df.columns:
                if "date" not in col:
                    continue
                try:
                    d = pd.to_datetime(df[col], errors="coerce").dropna()
                    if d.empty:
                        continue
                    date_columns.append({
                        "column": col,
                        "min": d.min().strftime("%Y-%m-%d"),
                        "max": d.max().strftime("%Y-%m-%d"),
                    })
                except Exception:
                    continue
            payload["tables"][name] = {
                "columns": list(df.columns),
                "dtypes": {c: str(df[c].dtype) for c in df.columns},
                "sample": df.head(3).astype(str).to_dict(orient="records"),
                "row_count": int(len(df)),
                "date_columns": date_columns,
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

    sql = parsed.get("sql")
    resp.sql = str(sql) if sql else resp.query
    if not resp.query and resp.sql:
        resp.query = resp.sql
    filters = parsed.get("filters")
    if isinstance(filters, dict):
        resp.filters = filters
    mv = parsed.get("matched_values")
    if isinstance(mv, dict):
        resp.matched_values = mv
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