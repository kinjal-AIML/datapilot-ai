"""Tests for the mock analyst engine and column resolution."""

from __future__ import annotations

import pandas as pd
import pytest

from analyst import MockAnalyst, resolve_column
from data_store import clean_column, read_tabular


def sample_sales() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2024-01-15", "2024-01-20", "2024-02-10", "2024-03-05", "2024-03-18"]
            ),
            "Customer": ["Acme", "Beta", "Acme", "Gamma", "Beta"],
            "Item Name": ["A", "B", "A", "C", "B"],
            "Country To": ["US", "US", "CA", "UK", "CA"],
            "Quantity": [10, 5, 20, 7, 3],
            "Value": [1000.0, 500.0, 2000.0, 700.0, 300.0],
            "Voucher No.": ["V1", "V2", "V3", "V4", "V5"],
        }
    )


def sample_purchase() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(["2024-01-10", "2024-02-01", "2024-03-01"]),
            "Vendor": ["V1", "V1", "V2"],
            "Item Name": ["A", "A", "B"],
            "Quantity": [50, 30, 20],
            "Value": [600.0, 400.0, 250.0],
            "Voucher No.": ["P1", "P2", "P3"],
        }
    )


@pytest.fixture
def sales_df() -> pd.DataFrame:
    df = sample_sales()
    df.columns = [clean_column(c) for c in df.columns]
    return df


@pytest.fixture
def purchase_df() -> pd.DataFrame:
    df = sample_purchase()
    df.columns = [clean_column(c) for c in df.columns]
    return df


def test_clean_column():
    assert clean_column("Item Name") == "item_name"
    assert clean_column("Gross Total") == "gross_total"
    assert clean_column("Country To") == "country_to"
    assert clean_column("Voucher No.") == "voucher_no"


def test_resolve_column(sales_df):
    assert resolve_column(sales_df, "revenue") == "value"
    assert resolve_column(sales_df, "product") == "item_name"
    assert resolve_column(sales_df, "customer") == "customer"
    assert resolve_column(sales_df, "country") == "country_to"
    assert resolve_column(sales_df, "id") == "voucher_no"
    assert resolve_column(sales_df, "nonexistent") is None


def test_summary(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("Give me a summary")
    assert resp.error is None
    assert resp.intent == "summary"
    assert resp.kpis["total_revenue"] == 4500.0
    assert resp.kpis["total_orders"] == 5
    # profit = 4500 - (600+400+250) = 3250
    assert resp.kpis["total_profit"] == 3250.0
    assert resp.chart_hint == "bar"
    assert resp.data


def test_monthly_profit_trend(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("What is the monthly profit?")
    assert resp.error is None
    assert resp.intent == "trend"
    assert resp.chart_hint == "line"
    assert "profit" in resp.metrics
    periods = [row["period"] for row in resp.data]
    assert periods == sorted(periods)  # chronological
    # Jan profit = 1500 - 600 = 900 ; Feb = 2000 - 400 = 1600 ; Mar = 1000 - 250 = 750
    by_period = {r["period"]: r["profit"] for r in resp.data}
    assert by_period["2024-01"] == 900.0
    assert by_period["2024-02"] == 1600.0
    assert by_period["2024-03"] == 750.0


def test_top_products(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("Top 3 products by revenue")
    assert resp.error is None
    assert resp.intent == "comparison"
    assert resp.chart_hint == "bar"
    assert resp.dimensions == ["product"]
    # A: 3000, B: 800, C: 700
    assert resp.data[0] == {"product": "A", "revenue": 3000.0}
    assert resp.data[1]["product"] == "B"


def test_top_customers(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("top 5 customers")
    assert resp.error is None
    assert resp.dimensions == ["customer"]
    # Acme: 3000, Beta: 800, Gamma: 700
    assert resp.data[0] == {"customer": "Acme", "revenue": 3000.0}


def test_revenue_by_country(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("Revenue by country")
    assert resp.error is None
    assert resp.intent == "distribution"
    # US: 1500, CA: 2300, UK: 700 -> 3 categories -> pie
    assert resp.chart_hint == "pie"
    by = {r["country"]: r["revenue"] for r in resp.data}
    assert by == {"CA": 2300.0, "US": 1500.0, "UK": 700.0}


def test_cost_by_vendor(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("Cost by vendor")
    assert resp.error is None
    assert "cost" in resp.metrics
    by = {r["vendor"]: r["cost"] for r in resp.data}
    assert by == {"V1": 1000.0, "V2": 250.0}


def test_missing_data_returns_error(sales_df):
    analyst = MockAnalyst(sales_df, None)
    resp = analyst.answer("Cost by vendor")
    assert resp.error is not None
    assert "purchase" in resp.error.lower() or "required" in resp.error.lower()


def test_empty_question(sales_df, purchase_df):
    analyst = MockAnalyst(sales_df, purchase_df)
    resp = analyst.answer("   ")
    assert resp.error == "Empty question"


def test_csv_roundtrip(tmp_path):
    csv_path = tmp_path / "sales.csv"
    sample_sales().to_csv(csv_path, index=False)
    df = read_tabular("sales.csv", csv_path.read_bytes())
    assert "item_name" in df.columns
    assert len(df) == 5
