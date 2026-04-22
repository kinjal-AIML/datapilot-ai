"""Flask integration tests — no network, no LLM required."""

from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from app import create_app


def build_sales_xlsx() -> bytes:
    df = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2024-01-10", "2024-02-12", "2024-03-08"]),
            "Customer": ["Acme", "Beta", "Acme"],
            "Item Name": ["A", "B", "A"],
            "Country To": ["US", "UK", "US"],
            "Quantity": [10, 5, 20],
            "Value": [1000.0, 500.0, 2000.0],
            "Voucher No.": ["V1", "V2", "V3"],
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def build_purchase_csv() -> bytes:
    df = pd.DataFrame(
        {
            "Date": ["2024-01-01", "2024-02-01"],
            "Vendor": ["V1", "V2"],
            "Item Name": ["A", "B"],
            "Quantity": [50, 20],
            "Value": [500.0, 250.0],
            "Voucher No.": ["P1", "P2"],
        }
    )
    return df.to_csv(index=False).encode("utf-8")


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert "llm_mode" in body


def test_upload_and_ask(client):
    data = {
        "sales": (io.BytesIO(build_sales_xlsx()), "sales.xlsx"),
        "purchase": (io.BytesIO(build_purchase_csv()), "purchase.csv"),
    }
    res = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert res.status_code == 200, res.data
    body = res.get_json()
    session_id = body["session_id"]
    assert set(body["tables"]) == {"sales", "purchase"}
    assert body["schema"]["sales"]["row_count"] == 3

    # Schema endpoint
    res = client.get(f"/api/schema?session_id={session_id}")
    assert res.status_code == 200

    # Summary question
    res = client.post(
        "/api/ask",
        data=json.dumps({"session_id": session_id, "question": "overall summary"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["error"] is None
    assert body["kpis"]["total_revenue"] == 3500.0
    assert body["kpis"]["total_profit"] == 2750.0

    # Trend question
    res = client.post(
        "/api/ask",
        data=json.dumps({"session_id": session_id, "question": "monthly revenue"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["chart_hint"] == "line"
    assert body["data"]


def test_upload_rejects_empty(client):
    res = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert res.status_code == 400


def test_ask_requires_session(client):
    res = client.post(
        "/api/ask",
        data=json.dumps({"session_id": "missing", "question": "hi"}),
        content_type="application/json",
    )
    assert res.status_code == 404
